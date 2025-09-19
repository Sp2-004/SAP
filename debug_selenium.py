#!/usr/bin/env python3
"""
Debug script to test Selenium WebDriver setup
"""
import os
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_chrome_setup():
    """Test Chrome/Chromium setup"""
    print("=== Testing Chrome Setup ===")
    
    # Check for Chrome binaries
    candidate_bins = [
        os.environ.get("CHROME_BIN"),
        "/opt/render/project/src/.chrome-for-testing/chrome-linux64/chrome",
        "/opt/render/project/src/.chrome-for-testing/chrome-linux64/chrome",
        "/app/.chrome-for-testing/chrome-linux64/chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/chrome",
    ]
    
    chrome_binary = None
    for binary in candidate_bins:
        if binary and os.path.isfile(binary):
            chrome_binary = binary
            print(f"✓ Found Chrome binary: {binary}")
            break
    
    if not chrome_binary:
        print("✗ No Chrome binary found!")
        return False
    
    # Check for ChromeDriver
    candidate_drivers = [
        os.environ.get("CHROMEDRIVER_PATH"),
        "/opt/render/project/src/.chrome-for-testing/chromedriver-linux64/chromedriver",
        "/opt/render/project/src/.chrome-for-testing/chromedriver-linux64/chromedriver",
        "/app/.chrome-for-testing/chromedriver-linux64/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
        "/usr/bin/chromedriver",
        "/usr/lib/chromium/chromedriver",
    ]
    
    chromedriver_path = None
    for driver in candidate_drivers:
        if driver and os.path.isfile(driver):
            chromedriver_path = driver
            print(f"✓ Found ChromeDriver: {driver}")
            break
    
    if not chromedriver_path:
        print("! No ChromeDriver found in standard locations, trying webdriver-manager...")
        try:
            chromedriver_path = ChromeDriverManager().install()
            print(f"✓ ChromeDriver installed via webdriver-manager: {chromedriver_path}")
        except Exception as e:
            print(f"✗ Failed to install ChromeDriver: {e}")
            return False
    
    return chrome_binary, chromedriver_path

def test_webdriver():
    """Test WebDriver initialization"""
    print("\n=== Testing WebDriver ===")
    
    chrome_binary, chromedriver_path = test_chrome_setup()
    if not chrome_binary or not chromedriver_path:
        return False
    
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.binary_location = chrome_binary
    
    try:
        service = Service(chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)
        print("✓ WebDriver initialized successfully")
        
        # Test navigation
        driver.get("https://www.google.com")
        title = driver.title
        print(f"✓ Successfully navigated to Google, title: {title}")
        
        driver.quit()
        print("✓ WebDriver closed successfully")
        return True
        
    except Exception as e:
        print(f"✗ WebDriver test failed: {e}")
        return False

def test_college_website():
    """Test access to college website"""
    print("\n=== Testing College Website Access ===")
    
    chrome_binary, chromedriver_path = test_chrome_setup()
    if not chrome_binary or not chromedriver_path:
        return False
    
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.binary_location = chrome_binary
    
    try:
        service = Service(chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)
        
        # Test college website
        college_url = "https://samvidha.iare.ac.in/"
        print(f"Navigating to: {college_url}")
        driver.get(college_url)
        
        title = driver.title
        print(f"✓ Page title: {title}")
        
        # Check for login elements
        try:
            username_field = driver.find_element(By.ID, "txt_uname")
            password_field = driver.find_element(By.ID, "txt_pwd")
            submit_button = driver.find_element(By.ID, "but_submit")
            print("✓ Login form elements found")
        except Exception as e:
            print(f"✗ Login form elements not found: {e}")
            # Try to find any input elements
            inputs = driver.find_elements(By.TAG_NAME, "input")
            print(f"Found {len(inputs)} input elements on the page")
        
        driver.quit()
        return True
        
    except Exception as e:
        print(f"✗ College website test failed: {e}")
        return False

if __name__ == "__main__":
    print("Starting Selenium Debug Tests...")
    
    success = True
    success &= test_webdriver()
    success &= test_college_website()
    
    if success:
        print("\n🎉 All tests passed! Selenium setup should work.")
    else:
        print("\n❌ Some tests failed. Check the errors above.")