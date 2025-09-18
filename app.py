import hashlib
import os
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

# Monkey-patch for openssl_md5() compatibility with Werkzeug's secure_filename
if hasattr(hashlib, "md5"):
    _original_md5 = hashlib.md5
    def _md5_patch(*args, **kwargs):
        return _original_md5(*args)
    hashlib.md5 = _md5_patch

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-secret")

COLLEGE_LOGIN_URL = "https://samvidha.iare.ac.in/"
ATTENDANCE_URL = "https://samvidha.iare.ac.in/home?action=course_content"

# Optional: Upstash Redis cache (falls back to in-memory)
UP_REDIS_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UP_REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

redis_client = None
if UP_REDIS_URL and UP_REDIS_TOKEN:
    try:
        redis_client = Redis(url=UP_REDIS_URL, token=UP_REDIS_TOKEN)
    except Exception:
        redis_client = None

_inmem_cache = {}

def cache_set(key, value, ttl_seconds=1800):
    if redis_client:
        try:
            redis_client.set(key, json.dumps(value), ex=ttl_seconds)
            return
        except Exception:
            pass
    _inmem_cache[key] = (time.time() + ttl_seconds, value)

def cache_get(key):
    if redis_client:
        try:
            v = redis_client.get(key)
            return json.loads(v) if v else None
        except Exception:
            pass
    entry = _inmem_cache.get(key)
    if not entry:
        return None
    exp, val = entry
    if time.time() > exp:
        _inmem_cache.pop(key, None)
        return None
    return val

def _build_chrome_options():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # Explicitly set Chromium binary
    chromium_bin = "/usr/bin/chromium"
    if os.path.isfile(chromium_bin):
        options.binary_location = chromium_bin
        print(f"DEBUG: Using Chromium binary: {chromium_bin}")
    else:
        print(f"DEBUG: Chromium binary not found at {chromium_bin}")
    
    # Check fallback Chrome binaries
    candidate_bins = [
        os.environ.get("CHROME_BIN"),
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for b in candidate_bins:
        if b and os.path.isfile(b):
            options.binary_location = b
            print(f"DEBUG: Using fallback binary: {b}")
            break
    return options

def _create_chromedriver_service():
    # Explicitly set Chromium-driver path
    chromedriver_path = "/usr/bin/chromedriver"
    if os.path.isfile(chromedriver_path):
        print(f"DEBUG: Using Chromium-driver: {chromedriver_path}")
        return Service(chromedriver_path)
    
    # Check fallback paths
    candidates = [
        os.environ.get("CHROMEDRIVER_PATH"),
        "/usr/lib/chromium/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
        "/usr/bin/chromedriver",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            print(f"DEBUG: Using fallback Chromium-driver: {p}")
            return Service(p)
    
    # Fallback to webdriver-manager
    print("DEBUG: Falling back to webdriver-manager")
    try:
        path = ChromeDriverManager().install()
        print(f"DEBUG: Webdriver-manager installed ChromeDriver at: {path}")
        return Service(path)
    except Exception as e:
        print(f"DEBUG: Webdriver-manager failed: {str(e)}")
        return Service()

def get_attendance_data(username, password):
    options = _build_chrome_options()
    driver = webdriver.Chrome(service=_create_chromedriver_service(), options=options)

    try:
        driver.get(COLLEGE_LOGIN_URL)
        time.sleep(2)
        print(f"DEBUG: Loaded login page: {driver.current_url}")
        print(driver.page_source[:1000])

        try:
            driver.find_element(By.ID, "txt_uname").send_keys(username)
            driver.find_element(By.ID, "txt_pwd").send_keys(password)
            driver.find_element(By.ID, "but_submit").click()
        except Exception as e:
            print(f"DEBUG: Login error: {str(e)}")
            inputs = driver.find_elements(By.TAG_NAME, "input")
            if len(inputs) >= 2:
                inputs[0].send_keys(username)
                inputs[1].send_keys(password)
                try:
                    driver.find_element(By.ID, "but_submit").click()
                except:
                    driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
            else:
                print(driver.page_source[:1000])
                raise Exception("Could not find login input fields")

        time.sleep(3)
        if "home" not in driver.current_url:
            return {"error": "Invalid username or password."}

        try:
            attendance_link = driver.find_element(By.LINK_TEXT, "Course Content")
            attendance_link.click()
        except:
            driver.get(ATTENDANCE_URL)

        time.sleep(3)
        rows = driver.find_elements(By.TAG_NAME, "tr")

        if not rows:
            return {"error": "No attendance data found (maybe server issue)."}

        return calculate_attendance_percentage(rows)

    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}")
        return {"error": f"Exception: {str(e)}"}
    finally:
        driver.quit()

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

            date_match = re.search(r'(\d{1,2}\s[A-Za-z]{3},?\s\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{1,2}\s[A-Za-z]{3})', text)
            if date_match:
                date_str = date_match.group(1).strip()
                
                try:
                    if ',' in date_str:
                        date_str = date_str.replace(',', '').strip()
                        dt = datetime.strptime(date_str, "%d %b %Y")
                    elif re.match(r'\d{1,2}\s[A-Za-z]{3}\s\d{4}', date_str):
                        dt = datetime.strptime(date_str, "%d %b %Y")
                    elif re.match(r'\d{1,2}\s[A-Za-z]{3}', date_str):
                        dt = datetime.strptime(f"{date_str} 2025", "%d %b %Y")
                    elif re.match(r'\d{1,2}[-/]\d{1,2}[-/]\d{4}', date_str):
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

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if request.method == "GET":
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
    
    username = request.form["username"]
    password = request.form["password"]

    data = get_attendance_data(username, password)

    if "error" in data:
        return render_template("login.html", error=data["error"])

    session['attendance_data'] = data
    session['username'] = username
    session['password'] = password

    try:
        cache_set(f"att:{username}", data, ttl_seconds=1800)
    except Exception:
        pass

    calendar_data = []
    date_attendance = data.get('date_attendance', {})
    
    print("DEBUG: date_attendance =", date_attendance)
    
    for date_key in date_attendance:
        try:
            dt = datetime.strptime(date_key, "%d-%m-%Y")
            present_cnt = date_attendance[date_key]['present']
            absent_cnt = date_attendance[date_key]['absent']
            value = 1 if present_cnt > 0 else (-1 if absent_cnt > 0 else 0)
            calendar_data.append({'date': dt.strftime("%Y-%m-%d"), 'value': value})
        except ValueError:
            print(f"DEBUG: Failed to parse date: {date_key}")
            continue
    
    print("DEBUG: calendar_data =", calendar_data)

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
    options = _build_chrome_options()
    driver = webdriver.Chrome(service=_create_chromedriver_service(), options=options)

    try:
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
                print(driver.page_source[:1000])
                raise Exception("Could not find login input fields")
        time.sleep(3)

        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(3)

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
            print(f"Error finding lab dropdown: {e}")
            return []

    except Exception as e:
        print(f"Error fetching lab subjects: {e}")
        return []
    finally:
        driver.quit()

def get_lab_dates(username, password, lab_code):
    options = _build_chrome_options()
    driver = webdriver.Chrome(service=_create_chromedriver_service(), options=options)

    try:
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

        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(3)

        lab_select_element = driver.find_element(By.CSS_SELECTOR, "select")
        lab_select = Select(lab_select_element)
        lab_select.select_by_value(lab_code)
        time.sleep(2)

        lab_dates = []
        current_date = datetime.now()
        
        try:
            rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 5:
                    week_text = cells[0].text.strip()
                    subject_code = cells[1].text.strip()
                    experiment_title = cells[2].text.strip()
                    batch_no = cells[3].text.strip()
                    submission_date = cells[4].text.strip()
                    
                    is_available = True
                    try:
                        if submission_date and '-' in submission_date:
                            submission_dt = datetime.strptime(submission_date, "%d-%m-%Y")
                            is_available = submission_dt.date() >= current_date.date()
                    except ValueError:
                        is_available = True
                    
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
            print(f"Error parsing lab dates: {e}")

        return lab_dates

    except Exception as e:
        print(f"Error fetching lab dates: {e}")
        return []
    finally:
        driver.quit()

def get_experiment_title(username, password, lab_code, week_number):
    options = _build_chrome_options()
    driver = webdriver.Chrome(service=_create_chromedriver_service(), options=options)

    try:
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

        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(3)

        lab_select_element = driver.find_element(By.CSS_SELECTOR, "select")
        lab_select = Select(lab_select_element)
        lab_select.select_by_value(lab_code)
        time.sleep(2)

        try:
            rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 3:
                    week_text = cells[0].text.strip()
                    experiment_title = cells[2].text.strip()
                    week_match = re.search(r'Week-?(\d+)', week_text, re.IGNORECASE)
                    if week_match and week_match.group(1) == str(week_number):
                        return experiment_title
        except Exception as e:
            print(f"Error finding experiment title: {e}")

        return ""

    except Exception as e:
        print(f"Error fetching experiment title: {e}")
        return ""
    finally:
        driver.quit()

def compress_images_to_pdf(image_files, max_size_mb=1):
    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4

    for image_file in image_files:
        try:
            img = Image.open(image_file)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            img_width, img_height = img.size
            scale_w = (width - 40) / img_width
            scale_h = (height - 40) / img_height
            scale = min(scale_w, scale_h, 1.0)

            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            img = img.resize((new_width, new_height), Image.LANCZOS)

            temp_img_path = tempfile.mktemp(suffix='.jpg')
            img.save(temp_img_path, format='JPEG', quality=85, optimize=True)

            x = (width - new_width) / 2
            y = (height - new_height) / 2
            c.drawImage(temp_img_path, x, y, width=new_width, height=new_height)
            c.showPage()

            os.remove(temp_img_path)
        except Exception as e:
            print(f"Error processing image: {e}")
            continue

    c.save()
    pdf_buffer.seek(0)

    pdf_size = len(pdf_buffer.getvalue())
    max_size_bytes = max_size_mb * 1024 * 1024

    if pdf_size > max_size_bytes:
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
    options = _build_chrome_options()
    driver = webdriver.Chrome(service=_create_chromedriver_service(), options=options)

    try:
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
                print(driver.page_source[:1000])
                raise Exception("Could not find login input fields")

        time.sleep(3)

        driver.get("https://samvidha.iare.ac.in/home?action=labrecord_std")
        time.sleep(5)

        lab_select_element = driver.find_element(By.ID, "sub_code")
        driver.execute_script("arguments[0].scrollIntoView(true);", lab_select_element)
        lab_select = Select(lab_select_element)
        lab_select.select_by_value(lab_code)

        week_select_element = driver.find_element(By.ID, "week_no")
        driver.execute_script("arguments[0].scrollIntoView(true);", week_select_element)
        week_select = Select(week_select_element)

        week_value = None
        available_values = [opt.get_attribute('value') for opt in week_select.options]
        match = re.search(r'Week-?(\d+)', str(week_no))
        if match:
            possible_value = match.group(0)
            possible_number = match.group(1)
            if possible_value in available_values:
                week_value = possible_value
            elif possible_number in available_values:
                week_value = possible_number
            else:
                week_value = available_values[0]
        else:
            week_value = available_values[0]
        print("Selecting week value:", week_value)
        week_select.select_by_value(week_value)

        title_field = driver.find_element(By.ID, "exp_title")
        driver.execute_script("arguments[0].scrollIntoView(true);", title_field)
        title_field.clear()
        title_field.send_keys(title)

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
        driver.quit()

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
        try:
            lab_code = request.form.get('lab_code')
            week_no = request.form.get('week_no')
            title = request.form.get('title')
            images = sorted(request.files.getlist('images'), key=lambda f: f.filename)
            
            if not all([lab_code, week_no, title]) or not images:
                return render_template("lab.html", data=data, error="Missing required data for upload")
            
            username = session.get('username')
            password = session.get('password')
            
            if not username or not password:
                return render_template("lab.html", data=data, error="Session expired. Please login again.")
            
            pdf_file = compress_images_to_pdf(images)
            
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

if __name__ == "__main__":
    app.run(debug=True)
