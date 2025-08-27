from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from tabulate import tabulate
import time
import re
from datetime import datetime
import json

COLLEGE_LOGIN_URL = "https://samvidha.iare.ac.in/"
ATTENDANCE_URL = "https://samvidha.iare.ac.in/home?action=course_content"

def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    # 🔑 webdriver-manager automatically downloads correct ChromeDriver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

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

def login_and_get_attendance(username, password):
    driver = create_driver()
    try:
        driver.get(COLLEGE_LOGIN_URL)
        time.sleep(2)

        driver.find_element(By.ID, "txt_uname").send_keys(username)
        driver.find_element(By.ID, "txt_pwd").send_keys(password)
        driver.find_element(By.ID, "but_submit").click()
        time.sleep(3)

        if driver.current_url != COLLEGE_LOGIN_URL:
            driver.get(ATTENDANCE_URL)
            time.sleep(3)
            rows = driver.find_elements(By.TAG_NAME, "tr")
            return calculate_attendance_percentage(rows)
        else:
            return {
                "overall": {
                    "success": False,
                    "message": "ERROR occurred: Please check username or password."
                }
            }

    except Exception as e:
        return {
            "overall": {
                "success": False,
                "message": f"Error: {str(e)}"
            }
        }
    finally:
        driver.quit()
        
if __name__ == "__main__":
    result = login_and_get_attendance("23951a67e1", "Satya@9100")
    print(json.dumps(result, indent=2))
