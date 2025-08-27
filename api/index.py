import hashlib

# Monkey-patch for openssl_md5() compatibility with Werkzeug's secure_filename
if hasattr(hashlib, "md5"):
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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from tabulate import tabulate
import time
import re
from datetime import datetime
import os
from PIL import Image
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
import tempfile
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

COLLEGE_LOGIN_URL = "https://samvidha.iare.ac.in/"
ATTENDANCE_URL = "https://samvidha.iare.ac.in/home?action=course_content"

def get_chrome_options():
    """Get Chrome options optimized for serverless environment"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--single-process")
    options.add_argument("--no-zygote")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")
    options.add_argument("--disable-javascript")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    
    # For Vercel deployment
    if os.environ.get('VERCEL'):
        options.binary_location = "/opt/google/chrome/chrome"
    
    return options

def create_driver():
    """Create Chrome driver with proper configuration"""
    options = get_chrome_options()
    
    try:
        # Try to use system Chrome first (for Vercel)
        if os.environ.get('VERCEL'):
            driver = webdriver.Chrome(options=options)
        else:
            # For local development
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
        
        return driver
    except Exception as e:
        print(f"Error creating driver: {e}")
        raise

def get_attendance_data(username, password):
    driver = None
    try:
        driver = create_driver()
        driver.set_page_load_timeout(30)
        
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
        
        # Better login check
        if "home" not in driver.current_url:
            return {"error": "Invalid username or password."}

        # Navigate to attendance page
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
        print("DEBUG ERROR:", str(e))
        return {"error": f"Exception: {str(e)}"}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

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

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if request.method == "GET":
        # Handle GET requests (navigation from other pages)
        data = session.get('attendance_data')
        if not data:
            return redirect("/")
        
        calendar_data = []
        date_attendance = data.get('date_attendance', {})
        
        for date_key in date_attendance:
            try:
                dt = datetime.strptime(date_key, "%d-%m-%Y")
                value = 1 if date_attendance[date_key]['present'] > 0 else 0
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

    data = get_attendance_data(username, password)

    if "error" in data:
        return render_template("login.html", error=data["error"])

    session['attendance_data'] = data
    session['username'] = username
    session['password'] = password

    calendar_data = []
    date_attendance = data.get('date_attendance', {})
    
    for date_key in date_attendance:
        try:
            dt = datetime.strptime(date_key, "%d-%m-%Y")
            value = 1 if date_attendance[date_key]['present'] > 0 else 0
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
        # Lab functionality disabled for serverless deployment
        return render_template("lab.html", data=data, error="Lab upload feature is temporarily disabled in this deployment.")
    
    return render_template("lab.html", data=data)

@app.route("/get_lab_subjects", methods=["POST"])
def get_lab_subjects_route():
    """API endpoint to fetch lab subjects - disabled for serverless"""
    return {"error": "Lab features temporarily disabled"}, 503

@app.route("/get_lab_dates", methods=["POST"])
def get_lab_dates_route():
    """API endpoint to fetch lab dates - disabled for serverless"""
    return {"error": "Lab features temporarily disabled"}, 503

@app.route("/get_experiment_title", methods=["POST"])
def get_experiment_title_route():
    """API endpoint to fetch experiment title - disabled for serverless"""
    return {"error": "Lab features temporarily disabled"}, 503

@app.route("/profile", methods=["GET"])
def profile():
    data = session.get('attendance_data')
    return render_template("profile.html", data=data)

@app.route("/ping", methods=["GET"])
def ping():
    return "pong", 200

# For Vercel deployment
if __name__ == "__main__":
    app.run(debug=False)