import hashlib

# Monkey-patch for openssl_md5() compatibility with Werkzeug's secure_filename
if hashlib.md5:
    _original_md5 = hashlib.md5
    def _md5_patch(*args, **kwargs):
        return _original_md5(*args)
    hashlib.md5 = _md5_patch

# Now import everything else
from flask import Flask, render_template, request, session, redirect, url_for
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from tabulate import tabulate
import time
import re
from datetime import datetime
import os
from PIL import Image
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
import json
import tempfile
from werkzeug.utils import secure_filename
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from upstash_redis import Redis
import logging
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import queue
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-secret")

COLLEGE_LOGIN_URL = "https://samvidha.iare.ac.in/"
ATTENDANCE_URL = "https://samvidha.iare.ac.in/home?action=course_content"

# WebDriver pool for handling concurrent requests
class WebDriverPool:
    def __init__(self, max_drivers=10):
        self.max_drivers = max_drivers
        self.available_drivers = queue.Queue()
        self.active_drivers = set()
        self.lock = threading.Lock()
        
    def get_driver(self, timeout=30):
        """Get a WebDriver instance from the pool"""
        try:
            # Try to get an existing driver
            driver = self.available_drivers.get_nowait()
            with self.lock:
                self.active_drivers.add(driver)
            return driver
        except queue.Empty:
            # Create new driver if under limit
            with self.lock:
                if len(self.active_drivers) < self.max_drivers:
                    try:
                        driver = self._create_driver()
                        self.active_drivers.add(driver)
                        logger.info(f"Created new WebDriver. Active: {len(self.active_drivers)}")
                        return driver
                    except Exception as e:
                        logger.error(f"Failed to create WebDriver: {e}")
                        raise
            
            # Wait for available driver
            try:
                driver = self.available_drivers.get(timeout=timeout)
                with self.lock:
                    self.active_drivers.add(driver)
                return driver
            except queue.Empty:
                raise TimeoutError("No WebDriver available within timeout")
    
    def return_driver(self, driver):
        """Return a WebDriver instance to the pool"""
        try:
            # Reset driver state
            driver.delete_all_cookies()
            driver.get("about:blank")
            
            with self.lock:
                self.active_drivers.discard(driver)
            self.available_drivers.put(driver)
        except Exception as e:
            logger.error(f"Error returning driver to pool: {e}")
            self._cleanup_driver(driver)
    
    def _create_driver(self):
        """Create a new WebDriver instance"""
        options = self._build_chrome_options()
        service = self._create_chromedriver_service()
        
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(30)
        driver.implicitly_wait(10)
        return driver
    
    def _build_chrome_options(self):
        """Build Chrome options for WebDriver"""
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-images")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        # Try to find Chrome binary
        candidate_bins = [
            os.environ.get("CHROME_BIN"),
            "/app/.chrome-for-testing/chrome-linux64/chrome",
            "/opt/render/project/src/.chrome-for-testing/chrome-linux64/chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/opt/google/chrome/chrome",
        ]
        
        for binary in candidate_bins:
            if binary and os.path.isfile(binary):
                options.binary_location = binary
                logger.info(f"Using Chrome binary: {binary}")
                break
        else:
            logger.warning("No Chrome binary found, using system default")
        
        return options
    
    def _create_chromedriver_service(self):
        """Create ChromeDriver service"""
        candidates = [
            os.environ.get("CHROMEDRIVER_PATH"),
            "/app/.chrome-for-testing/chromedriver-linux64/chromedriver",
            "/opt/render/project/src/.chrome-for-testing/chromedriver-linux64/chromedriver",
            "/usr/bin/chromedriver",
            "/usr/lib/chromium-browser/chromedriver",
            "/usr/lib/chromium/chromedriver",
        ]
        
        for path in candidates:
            if path and os.path.isfile(path):
                logger.info(f"Using ChromeDriver: {path}")
                return Service(path)
        
        # Fallback to webdriver-manager
        try:
            # Only try webdriver-manager if we have Chrome available
            chrome_available = any(
                binary and os.path.isfile(binary) 
                for binary in [
                    "/usr/bin/google-chrome",
                    "/usr/bin/google-chrome-stable",
                    "/usr/bin/chromium-browser",
                    "/usr/bin/chromium"
                ]
            )
            if chrome_available:
                path = ChromeDriverManager().install()
                logger.info(f"ChromeDriver installed via webdriver-manager: {path}")
                return Service(path)
            else:
                logger.error("No Chrome binary found for webdriver-manager")
                raise Exception("Chrome binary not found")
        except Exception as e:
            logger.error(f"Failed to install ChromeDriver: {e}")
            raise Exception(f"ChromeDriver setup failed: {e}")
    
    def _cleanup_driver(self, driver):
        """Clean up a WebDriver instance"""
        try:
            driver.quit()
        except Exception as e:
            logger.error(f"Error cleaning up driver: {e}")
        finally:
            with self.lock:
                self.active_drivers.discard(driver)
    
    def cleanup_all(self):
        """Clean up all WebDriver instances"""
        logger.info("Cleaning up WebDriver pool...")
        
        # Clean up available drivers
        while not self.available_drivers.empty():
            try:
                driver = self.available_drivers.get_nowait()
                self._cleanup_driver(driver)
            except queue.Empty:
                break
        
        # Clean up active drivers
        with self.lock:
            for driver in list(self.active_drivers):
                self._cleanup_driver(driver)

# Global WebDriver pool
driver_pool = WebDriverPool(max_drivers=3)  # Reduce concurrent drivers for Render

# Cleanup on exit
atexit.register(driver_pool.cleanup_all)
# Optional: Upstash Redis cache (falls back to in-memory)
UP_REDIS_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UP_REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

redis_client = None
if UP_REDIS_URL and UP_REDIS_TOKEN:
    try:
        redis_client = Redis(url=UP_REDIS_URL, token=UP_REDIS_TOKEN)
        logger.info("Connected to Upstash Redis")
    except Exception:
        redis_client = None
        logger.warning("Failed to connect to Redis, using in-memory cache")

_inmem_cache = {}

def cache_set(key, value, ttl_seconds=1800):
    if redis_client:
        try:
            redis_client.set(key, json.dumps(value), ex=ttl_seconds)
            return
        except Exception as e:
            logger.error(f"Redis cache set error: {e}")
            pass
    _inmem_cache[key] = (time.time() + ttl_seconds, value)

def cache_get(key):
    if redis_client:
        try:
            v = redis_client.get(key)
            return json.loads(v) if v else None
        except Exception as e:
            logger.error(f"Redis cache get error: {e}")
            pass
    entry = _inmem_cache.get(key)
    if not entry:
        return None
    exp, val = entry
    if time.time() > exp:
        _inmem_cache.pop(key, None)
        return None
    return val

def get_attendance_data(username, password):
    """Get attendance data using WebDriver pool"""
    driver = None
    try:
        # Get driver from pool
        driver = driver_pool.get_driver(timeout=30)
        logger.info(f"Got WebDriver for user: {username}")
        
        return _scrape_attendance_data(driver, username, password)
        
    except TimeoutError:
        logger.error("Timeout waiting for WebDriver")
        return {"error": "System busy, please try again in a moment"}
    except Exception as e:
        if "Chrome binary not found" in str(e) or "ChromeDriver setup failed" in str(e):
            logger.error(f"Chrome setup error: {e}")
            return {"error": "Server configuration issue. Please contact administrator."}
        logger.error(f"Error getting attendance data: {e}")
        return {"error": f"System error: {str(e)}"}
    finally:
        if driver:
            driver_pool.return_driver(driver)

def _scrape_attendance_data(driver, username, password):
    """Scrape attendance data using provided WebDriver"""
    try:
        logger.info(f"Starting attendance scrape for user: {username}")
        driver.get(COLLEGE_LOGIN_URL)
        time.sleep(2)

        try:
            driver.find_element(By.ID, "txt_uname").send_keys(username)
            driver.find_element(By.ID, "txt_pwd").send_keys(password)
            driver.find_element(By.ID, "but_submit").click()
        except Exception:
            # Fallback: Try generic input selection
            inputs = driver.find_elements(By.TAG_NAME, "input")
            if len(inputs) >= 2:
                inputs[0].send_keys(username)
                inputs[1].send_keys(password)
                # Try to find and click the login button
                try:
                    driver.find_element(By.ID, "but_submit").click()
                except:
                    driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
            else:
                raise Exception("Could not find login input fields")

        time.sleep(3)
        # Better login check
        if "home" not in driver.current_url:
            logger.warning(f"Login failed for user: {username}")
            return {"error": "Invalid username or password."}

        # Instead of forcing get(), click the menu item for Attendance
        try:
            attendance_link = driver.find_element(By.LINK_TEXT, "Course Content")
            attendance_link.click()
        except:
            driver.get(ATTENDANCE_URL)

        time.sleep(3)
        rows = driver.find_elements(By.TAG_NAME, "tr")

        if not rows:
            logger.warning(f"No attendance data found for user: {username}")
            return {"error": "No attendance data found (maybe server issue)."}

        logger.info(f"Successfully scraped attendance data for user: {username}")
        return calculate_attendance_percentage(rows)

    except Exception as e:
        logger.error(f"Scraping error for user {username}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"error": f"Exception: {str(e)}"}

def calculate_attendance_percentage(rows):
    result = {
        "subjects": {},
        "overall": {
            "present": 0,
            "absent": 0,
            "percentage": 0.0,
            "success": False,
            "message": ""
        },
        "date_attendance": {},
        "per_course_date_attendance": {},
        "streak": 0,
        "attended_days": 0,
        "absent_days": 0,
        "safe_bunk_days": 0
    }

    current_course = None
    total_present = 0
    total_absent = 0
    date_attendance = {}
    per_course_date_attendance = {}

    for row in rows:
        text = row.text.strip().upper()
        if not text or text.startswith("S.NO") or "TOPICS COVERED" in text:
            continue

        course_match = re.match(r"^(A[A-Z]+\d+|ACDD05)\s*[-:\s]+\s*(.+)$", text)
        if course_match:
            current_course = course_match.group(1)
            course_name = course_match.group(2).strip()
            result["subjects"][current_course] = {
                "name": course_name,
                "present": 0,
                "absent": 0,
                "percentage": 0.0
            }
            per_course_date_attendance[current_course] = {}
            continue

        if current_course:
            present_count = text.count("PRESENT")
            absent_count = text.count("ABSENT")
            result["subjects"][current_course]["present"] += present_count
            result["subjects"][current_course]["absent"] += absent_count
            total_present += present_count
            total_absent += absent_count

            # Enhanced date matching for various formats
            date_match = re.search(r'(\d{1,2}\s[A-Za-z]{3},?\s\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{1,2}\s[A-Za-z]{3})', text)
            if date_match:
                date_str = date_match.group(1).strip()
                
                # Convert various date formats to DD-MM-YYYY
                try:
                    if ',' in date_str:
                        # Format: "20 Aug, 2025" or "20 Aug,2025"
                        date_str = date_str.replace(',', '').strip()
                        dt = datetime.strptime(date_str, "%d %b %Y")
                    elif re.match(r'\d{1,2}\s[A-Za-z]{3}\s\d{4}', date_str):
                        # Format: "20 Aug 2025"
                        dt = datetime.strptime(date_str, "%d %b %Y")
                    elif re.match(r'\d{1,2}\s[A-Za-z]{3}', date_str):
                        # Format: "20 Aug" (assume current year)
                        dt = datetime.strptime(f"{date_str} 2025", "%d %b %Y")
                    elif re.match(r'\d{1,2}[-/]\d{1,2}[-/]\d{4}', date_str):
                        # Format: "20-08-2025" or "20/08/2025"
                        date_str = date_str.replace('/', '-')
                        dt = datetime.strptime(date_str, "%d-%m-%Y")
                    else:
                        continue
                    
                    date_key = dt.strftime("%d-%m-%Y")
                except (ValueError, AttributeError):
                    continue
                
                if date_key not in date_attendance:
                    date_attendance[date_key] = {'present': 0, 'absent': 0}
                date_attendance[date_key]['present'] += present_count
                date_attendance[date_key]['absent'] += absent_count

                if date_key not in per_course_date_attendance[current_course]:
                    per_course_date_attendance[current_course][date_key] = {'present': 0, 'absent': 0}
                per_course_date_attendance[current_course][date_key]['present'] += present_count
                per_course_date_attendance[current_course][date_key]['absent'] += absent_count

    for sub_key, sub in result["subjects"].items():
        total = sub["present"] + sub["absent"]
        if total > 0:
            sub["percentage"] = round((sub["present"] / total) * 100, 2)
        sub["safe_bunk_periods"] = max(0, sub["present"] // 3 - sub["absent"])

        course_dates = per_course_date_attendance.get(sub_key, {})
        sub["attended_days"] = len([d for d in course_dates if course_dates[d]['present'] > 0])
        sub["absent_days"] = len([d for d in course_dates if course_dates[d]['present'] == 0 and course_dates[d]['absent'] > 0])
        sub["safe_bunk_days"] = max(0, sub["attended_days"] // 3 - sub["absent_days"])

    overall_total = total_present + total_absent
    if overall_total > 0:
        overall_percentage = round((total_present / overall_total) * 100, 2)
        result["overall"] = {
            "present": total_present,
            "absent": total_absent,
            "percentage": overall_percentage,
            "success": True,
            "message": f"Overall Attendance: Present = {total_present}, Absent = {total_absent}, Percentage = {overall_percentage}%",
            "safe_bunk_periods": max(0, total_present // 3 - total_absent)
        }

    result["date_attendance"] = date_attendance
    result["per_course_date_attendance"] = per_course_date_attendance

    # Calculate streak and other date-based metrics
    if date_attendance:
        try:
            dates = sorted(date_attendance.keys(), key=lambda x: datetime.strptime(x, "%d-%m-%Y"))
        except ValueError:
            dates = list(date_attendance.keys())
            
        streak = 0
        for d in reversed(dates):
            if date_attendance[d]['present'] > 0:
                streak += 1
            else:
                break
        result["streak"] = streak
        result["attended_days"] = len([d for d in date_attendance if date_attendance[d]['present'] > 0])
        result["absent_days"] = len([d for d in date_attendance if date_attendance[d]['present'] == 0 and date_attendance[d]['absent'] > 0])
        result["safe_bunk_days"] = max(0, result["attended_days"] // 3 - result["absent_days"])

    return result

@app.route("/", methods=["GET"])
def login_page():
    return render_template("login.html")

@app.route("/favicon.ico")
def favicon():
    # Return the IARE favicon or a 204 No Content
    from flask import abort
    abort(204)


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if request.method == "GET":
        # Handle GET requests (navigation from other pages)
        data = session.get('attendance_data')
        if not data:
            username = session.get('username')
            if username:
                cached = cache_get(f"att:{username}")
                if cached:
                    data = cached
        if not data:
            return redirect("/")
        
        calendar_data = []
        date_attendance = data.get('date_attendance', {})
        
        for date_key in date_attendance:
            try:
                dt = datetime.strptime(date_key, "%d-%m-%Y")
                # 1 = present, -1 = absent, 0 = holiday (no record)
                present_cnt = date_attendance[date_key]['present']
                absent_cnt = date_attendance[date_key]['absent']
                value = 1 if present_cnt > 0 else (-1 if absent_cnt > 0 else 0)
                calendar_data.append({'date': dt.strftime("%Y-%m-%d"), 'value': value})
            except ValueError:
                continue
        
        table_data = []
        for i, (code, sub) in enumerate(data["subjects"].items(), start=1):
            table_data.append([i, code, sub["name"], sub["present"], sub["absent"], f"{sub['percentage']}%"])

        table_html = tabulate(
            table_data,
            headers=["S.No", "Course Code", "Course Name", "Present", "Absent", "Percentage"],
            tablefmt="html"
        )
        
        return render_template("dashboard.html", data=data, calendar_data=calendar_data, table_html=table_html)
    
    # Handle POST requests (login)
    username = request.form["username"]
    password = request.form["password"]

    # Check cache first
    cached_data = cache_get(f"att:{username}")
    if cached_data:
        logger.info(f"Using cached data for user: {username}")
        session['attendance_data'] = cached_data
        session['username'] = username
        session['password'] = password
        
        calendar_data = []
        date_attendance = cached_data.get('date_attendance', {})
        
        for date_key in date_attendance:
            try:
                dt = datetime.strptime(date_key, "%d-%m-%Y")
                present_cnt = date_attendance[date_key]['present']
                absent_cnt = date_attendance[date_key]['absent']
                value = 1 if present_cnt > 0 else (-1 if absent_cnt > 0 else 0)
                calendar_data.append({'date': dt.strftime("%Y-%m-%d"), 'value': value})
            except ValueError:
                continue
        
        table_data = []
        for i, (code, sub) in enumerate(cached_data["subjects"].items(), start=1):
            table_data.append([i, code, sub["name"], sub["present"], sub["absent"], f"{sub['percentage']}%"])

        table_html = tabulate(
            table_data,
            headers=["S.No", "Course Code", "Course Name", "Present", "Absent", "Percentage"],
            tablefmt="html"
        )

        return render_template("dashboard.html", data=cached_data, calendar_data=calendar_data, table_html=table_html)

    # Scrape fresh data
    data = get_attendance_data(username, password)

    if "error" in data:
        return render_template("login.html", error=data["error"])

    session['attendance_data'] = data
    session['username'] = username
    session['password'] = password

    try:
        cache_set(f"att:{username}", data, ttl_seconds=1800)
        logger.info(f"Cached attendance data for user: {username}")
    except Exception:
        pass

    calendar_data = []
    date_attendance = data.get('date_attendance', {})
    
    for date_key in date_attendance:
        try:
            dt = datetime.strptime(date_key, "%d-%m-%Y")
            present_cnt = date_attendance[date_key]['present']
            absent_cnt = date_attendance[date_key]['absent']
            value = 1 if present_cnt > 0 else (-1 if absent_cnt > 0 else 0)
            calendar_data.append({'date': dt.strftime("%Y-%m-%d"), 'value': value})
        except ValueError:
            continue
    
    table_data = []
    for i, (code, sub) in enumerate(data["subjects"].items(), start=1):
        table_data.append([i, code, sub["name"], sub["present"], sub["absent"], f"{sub['percentage']}%"])

    table_html = tabulate(
        table_data,
        headers=["S.No", "Course Code", "Course Name", "Present", "Absent", "Percentage"],
        tablefmt="html"
    )

    return render_template("dashboard.html", data=data, calendar_data=calendar_data, table_html=table_html)

def get_lab_subjects(username, password):
    """Fetch lab subjects from the website"""
    driver = None

    try:
        driver = driver_pool.get_driver(timeout=30)
        # Login
        driver.get(COLLEGE_LOGIN_URL)
        time.sleep(2)
        try:
            driver.find_element(By.ID, "txt_uname").send_keys(username)
            driver.find_element(By.ID, "txt_pwd").send_keys(password)
            driver.find_element(By.ID, "but_submit").click()
        except Exception:
            # Fallback: Try generic input selection
            inputs = driver.find_elements(By.TAG_NAME, "input")
            if len(inputs) >= 2:
                inputs[0].send_keys(username)
                inputs[1].send_keys(password)
                try:
                    driver.find_element(By.ID, "but_submit").click()
                except:
                    driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
            else:
                raise Exception("Could not find login input fields")
        time.sleep(3)

        # Navigate to lab record page
        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(3)

        # Find the first select dropdown (Subject dropdown)
        try:
            lab_select_element = driver.find_element(By.CSS_SELECTOR, "select")
            lab_select = Select(lab_select_element)
            lab_options = []
            for option in lab_select.options:
                value = option.get_attribute('value')
                text = option.text
                if value and value.strip() and "select" not in text.lower():
                    lab_options.append({
                        'value': value,
                        'text': text
                    })
            return lab_options
        except Exception as e:
            logger.error(f"Error finding lab dropdown: {e}")
            return []

    except Exception as e:
        logger.error(f"Error fetching lab subjects: {e}")
        return []
    finally:
        if driver:
            driver_pool.return_driver(driver)

def get_lab_dates(username, password, lab_code):
    """Fetch available lab dates and experiment details for a specific lab"""
    driver = None

    try:
        driver = driver_pool.get_driver(timeout=30)
        # Login
        driver.get(COLLEGE_LOGIN_URL)
        time.sleep(2)
        try:
            driver.find_element(By.ID, "txt_uname").send_keys(username)
            driver.find_element(By.ID, "txt_pwd").send_keys(password)
            driver.find_element(By.ID, "but_submit").click()
        except Exception:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            if len(inputs) >= 2:
                inputs[0].send_keys(username)
                inputs[1].send_keys(password)
                try:
                    driver.find_element(By.ID, "but_submit").click()
                except:
                    driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
        time.sleep(3)

        # Navigate to lab record page
        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(3)

        # Select the lab from first dropdown
        lab_select_element = driver.find_element(By.CSS_SELECTOR, "select")
        lab_select = Select(lab_select_element)
        lab_select.select_by_value(lab_code)
        time.sleep(2)

        # Parse the experiment details table and filter for available dates
        lab_dates = []
        current_date = datetime.now()
        
        try:
            # Look for table rows containing experiment data
            rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 5:  # Week#, Subject Code, Experiment Title, Batch No, Experiment Submission Date
                    week_text = cells[0].text.strip()
                    subject_code = cells[1].text.strip()
                    experiment_title = cells[2].text.strip()
                    batch_no = cells[3].text.strip()
                    submission_date = cells[4].text.strip()
                    
                    # Parse submission date to check if it's still open for upload
                    is_available = True
                    try:
                        # Parse date in DD-MM-YYYY format
                        if submission_date and '-' in submission_date:
                            submission_dt = datetime.strptime(submission_date, "%d-%m-%Y")
                            # Only show dates that are today or in the future
                            is_available = submission_dt.date() >= current_date.date()
                    except ValueError:
                        # If date parsing fails, assume it's available
                        is_available = True
                    
                    # Extract week number from week text (e.g., "Week-1" -> "1")
                    week_match = re.search(r'Week-?(\d+)', week_text, re.IGNORECASE)
                    if week_match and experiment_title and submission_date and is_available:
                        week_number = week_match.group(1)
                        lab_dates.append({
                            'week_number': week_number,
                            'week_text': week_text,
                            'subject_code': subject_code,
                            'experiment_title': experiment_title,
                            'batch_no': batch_no,
                            'submission_date': submission_date,
                            'is_available': is_available
                        })
        except Exception as e:
            logger.error(f"Error parsing lab dates: {e}")

        return lab_dates

    except Exception as e:
        logger.error(f"Error fetching lab dates: {e}")
        return []
    finally:
        if driver:
            driver_pool.return_driver(driver)

def get_experiment_title(username, password, lab_code, week_number):
    """Get experiment title for a specific lab and week"""
    driver = None

    try:
        driver = driver_pool.get_driver(timeout=30)
        # Login
        driver.get(COLLEGE_LOGIN_URL)
        time.sleep(2)
        try:
            driver.find_element(By.ID, "txt_uname").send_keys(username)
            driver.find_element(By.ID, "txt_pwd").send_keys(password)
            driver.find_element(By.ID, "but_submit").click()
        except Exception:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            if len(inputs) >= 2:
                inputs[0].send_keys(username)
                inputs[1].send_keys(password)
                try:
                    driver.find_element(By.ID, "but_submit").click()
                except:
                    driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
        time.sleep(3)

        # Navigate to lab record page
        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(3)

        # Select the lab from first dropdown
        lab_select_element = driver.find_element(By.CSS_SELECTOR, "select")
        lab_select = Select(lab_select_element)
        lab_select.select_by_value(lab_code)
        time.sleep(2)

        # Find the experiment title for the specific week
        try:
            rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 3:
                    week_text = cells[0].text.strip()
                    experiment_title = cells[2].text.strip()
                    
                    # Check if this is the week we're looking for
                    week_match = re.search(r'Week-?(\d+)', week_text, re.IGNORECASE)
                    if week_match and week_match.group(1) == str(week_number):
                        return experiment_title
        except Exception as e:
            logger.error(f"Error finding experiment title: {e}")

        return ""

    except Exception as e:
        logger.error(f"Error fetching experiment title: {e}")
        return ""
    finally:
        if driver:
            driver_pool.return_driver(driver)

def compress_images_to_pdf(image_files, max_size_mb=1):
    """Convert and compress images to PDF under specified size"""
    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4

    for image_file in image_files:
        try:
            img = Image.open(image_file)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # Calculate scaling to fit page
            img_width, img_height = img.size
            scale_w = (width - 40) / img_width
            scale_h = (height - 40) / img_height
            scale = min(scale_w, scale_h, 1.0)

            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            img = img.resize((new_width, new_height), Image.LANCZOS)

            # Save to a temporary file for ReportLab
            temp_img_path = tempfile.mktemp(suffix='.jpg')
            img.save(temp_img_path, format='JPEG', quality=85, optimize=True)

            x = (width - new_width) / 2
            y = (height - new_height) / 2
            c.drawImage(temp_img_path, x, y, width=new_width, height=new_height)
            c.showPage()

            # Clean up temp image
            os.remove(temp_img_path)
        except Exception as e:
            print(f"Error processing image: {e}")
            continue

    c.save()
    pdf_buffer.seek(0)

    # Check size and compress if needed
    pdf_size = len(pdf_buffer.getvalue())
    max_size_bytes = max_size_mb * 1024 * 1024

    if pdf_size > max_size_bytes:
        # Reduce quality and try again
        pdf_buffer = io.BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=A4)
        for image_file in image_files:
            try:
                img = Image.open(image_file)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img_width, img_height = img.size
                scale_w = (width - 40) / img_width
                scale_h = (height - 40) / img_height
                scale = min(scale_w, scale_h, 0.8)
                new_width = int(img_width * scale)
                new_height = int(img_height * scale)
                img = img.resize((new_width, new_height), Image.LANCZOS)
                temp_img_path = tempfile.mktemp(suffix='.jpg')
                img.save(temp_img_path, format='JPEG', quality=60, optimize=True)
                x = (width - new_width) / 2
                y = (height - new_height) / 2
                c.drawImage(temp_img_path, x, y, width=new_width, height=new_height)
                c.showPage()
                os.remove(temp_img_path)
            except Exception as e:
                continue
        c.save()
        pdf_buffer.seek(0)

    return pdf_buffer

def upload_lab_record(username, password, lab_code, week_no, title, pdf_file):
    driver = None

    try:
        driver = driver_pool.get_driver(timeout=30)
        # Login
        driver.get(COLLEGE_LOGIN_URL)
        time.sleep(2)

        try:
            driver.find_element(By.ID, "txt_uname").send_keys(username)
            driver.find_element(By.ID, "txt_pwd").send_keys(password)
            driver.find_element(By.ID, "but_submit").click()
        except Exception:
            inputs = driver.find_elements(By.TAG_NAME, "input")
            if len(inputs) >= 2:
                inputs[0].send_keys(username)
                inputs[1].send_keys(password)
                try:
                    driver.find_element(By.ID, "but_submit").click()
                except:
                    driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
            else:
                raise Exception("Could not find login input fields")

        time.sleep(3)

        # Navigate to lab record page
        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(5)

        # Use specific IDs for form fields
        lab_select_element = driver.find_element(By.ID, "sub_code")
        driver.execute_script("arguments[0].scrollIntoView(true);", lab_select_element)
        lab_select = Select(lab_select_element)
        lab_select.select_by_value(lab_code)

        week_select_element = driver.find_element(By.ID, "week_no")
        driver.execute_script("arguments[0].scrollIntoView(true);", week_select_element)
        week_select = Select(week_select_element)

        # Ensure week_value matches the actual option value
        week_value = None
        available_values = [opt.get_attribute('value') for opt in week_select.options]
        match = re.search(r'Week-?(\d+)', str(week_no))
        if match:
            possible_value = match.group(0)
            possible_number = match.group(1)
            # Try full "Week-7" first
            if possible_value in available_values:
                week_value = possible_value
            # Try just "7"
            elif possible_number in available_values:
                week_value = possible_number
            else:
                # fallback: use first available value
                week_value = available_values[0]
        else:
            week_value = available_values[0]
        logger.info(f"Selecting week value: {week_value}")
        week_select.select_by_value(week_value)

        title_field = driver.find_element(By.ID, "exp_title")
        driver.execute_script("arguments[0].scrollIntoView(true);", title_field)
        title_field.clear()
        title_field.send_keys(title)

        # Assert that the title field is correctly set
        assert title_field.get_attribute("value") == title

        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            temp_file.write(pdf_file.getvalue())
            temp_file_path = temp_file.name

        file_input = driver.find_element(By.ID, "prog_doc")
        driver.execute_script("arguments[0].scrollIntoView(true);", file_input)
        file_input.send_keys(temp_file_path)

        time.sleep(2)

        submit_button = driver.find_element(By.ID, "LAB_OK")
        driver.execute_script("arguments[0].scrollIntoView(true);", submit_button)
        submit_button.click()

        time.sleep(3)
        os.unlink(temp_file_path)
  
        page_source = driver.page_source.lower()
        if "success" in page_source or "uploaded" in page_source:
            return {"success": True, "message": "Lab record uploaded successfully!"}
        elif "error" in page_source or "failed" in page_source:
            return {"success": False, "message": "Upload failed. Please check your inputs and try again."}
        else:
            return {"success": True, "message": "Upload completed. Please verify on the website."}

    except Exception as e:
        return {"success": False, "message": f"Error uploading lab record: {str(e)}"}
    finally:
        if driver:
            driver_pool.return_driver(driver)

@app.route("/b_safe", methods=["GET"])
def b_safe():
    data = session.get('attendance_data')   
    if not data:
        return redirect("/")
    bunk = request.args.get('bunk', 0, type=int)
    total = data["overall"]["present"] + data["overall"]["absent"] + bunk
    projected = round((data["overall"]["present"] / total * 100) if total > 0 else 0, 2)
    return render_template("b_safe.html", data=data, bunk=bunk, projected=projected)

@app.route("/course/<code>", methods=["GET"])
def course(code):
    data = session.get('attendance_data')
    if not data or code not in data['subjects']:
        return redirect("/dashboard")
    sub = data['subjects'][code]
    bunk = request.args.get('bunk', 0, type=int)
    total = sub["present"] + sub["absent"] + bunk
    projected = round((sub["present"] / total * 100) if total > 0 else 0, 2)
    return render_template("course.html", sub=sub, code=code, bunk=bunk, projected=projected)

@app.route("/lab", methods=["GET", "POST"])
def lab():
    data = session.get('attendance_data')
    
    if request.method == "POST":
        # Handle lab record upload
        try:
            lab_code = request.form.get('lab_code')
            week_no = request.form.get('week_no')
            title = request.form.get('title')
            # Sort images by filename to preserve order
            images = sorted(request.files.getlist('images'), key=lambda f: f.filename)
            
            if not all([lab_code, week_no, title]) or not images:
                return render_template("lab.html", data=data, error="Missing required data for upload")
            
            # Get credentials from session or request
            username = session.get('username')
            password = session.get('password')
            
            if not username or not password:
                return render_template("lab.html", data=data, error="Session expired. Please login again.")
            
            # Compress images to PDF
            pdf_file = compress_images_to_pdf(images)
            
            # Upload to website
            result = upload_lab_record(username, password, lab_code, week_no, title, pdf_file)
            
            if result["success"]:
                return render_template("lab.html", data=data, success=result["message"])
            else:
                return render_template("lab.html", data=data, error=result["message"])
                
        except Exception as e:
            return render_template("lab.html", data=data, error=f"Error processing upload: {str(e)}")
    
    return render_template("lab.html", data=data)

@app.route("/get_lab_subjects", methods=["POST"])
def get_lab_subjects_route():
    """API endpoint to fetch lab subjects"""
    try:
        username = session.get('username')
        password = session.get('password')
        
        if not username or not password:
            return {"error": "Session expired"}, 401
        
        lab_subjects = get_lab_subjects(username, password)
        return {"subjects": lab_subjects}
        
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/get_lab_dates", methods=["POST"])
def get_lab_dates_route():
    """API endpoint to fetch lab dates for a specific lab"""
    try:
        username = session.get('username')
        password = session.get('password')
        lab_code = request.json.get('lab_code')
        
        if not username or not password:
            return {"error": "Session expired"}, 401
            
        if not lab_code:
            return {"error": "Lab code is required"}, 400
        
        lab_dates = get_lab_dates(username, password, lab_code)
        return {"dates": lab_dates}
        
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/get_experiment_title", methods=["POST"])
def get_experiment_title_route():
    """API endpoint to fetch experiment title for a specific lab and week"""
    try:
        username = session.get('username')
        password = session.get('password')
        lab_code = request.json.get('lab_code')
        week_number = request.json.get('week_number')
        
        if not username or not password:
            return {"error": "Session expired"}, 401
            
        if not lab_code or not week_number:
            return {"error": "Lab code and week number are required"}, 400
        
        experiment_title = get_experiment_title(username, password, lab_code, week_number)
        return {"title": experiment_title}
        
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/profile", methods=["GET"])
def profile():
    data = session.get('attendance_data')
    return render_template("profile.html", data=data)

@app.route("/ping", methods=["GET"])
def ping():
    return "pong", 200

def ensure_interactable(driver, element):
    driver.execute_script("arguments[0].scrollIntoView(true);", element)
    time.sleep(0.5)
    if not element.is_displayed() or not element.is_enabled():
        raise Exception("Element not interactable (not visible or not enabled)")

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return render_template('login.html', error="An internal server error occurred. Please try again."), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {str(e)}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    return render_template('login.html', error="An unexpected error occurred. Please try again."), 500

if __name__ == "__main__":
    app.run(debug=True)