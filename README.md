# Attendance Tracker

A Flask web application for tracking college attendance with calendar visualization and lab record upload functionality.

## Features

- 📊 **Attendance Dashboard** - View overall and subject-wise attendance percentages
- 📅 **Calendar Visualization** - See daily attendance streak with color-coded calendar
- 🧪 **Lab Record Upload** - Upload lab experiment records as PDF
- 🔒 **Secure Login** - College portal authentication
- 💾 **Caching** - Fast loading with Redis caching
- 📱 **Responsive Design** - Works on desktop and mobile

## Deployment

### Render (Recommended)

1. **Fork/Clone this repository**
2. **Create a new Web Service on Render**
3. **Configure settings:**
   - Environment: Python 3
   - Build Command: `pip install --upgrade pip && pip install -r requirements.txt`
   - Start Command: `python -m gunicorn app:app --bind=0.0.0.0:$PORT --workers=3 --threads=4 --timeout=180 --preload`
4. **Set Environment Variables:**
   - `FLASK_SECRET_KEY` - A long random string
   - `UPSTASH_REDIS_REST_URL` - Your Upstash Redis URL
   - `UPSTASH_REDIS_REST_TOKEN` - Your Upstash Redis token
5. **Deploy!**

### Environment Variables

- `FLASK_SECRET_KEY` - Flask secret key for sessions
- `UPSTASH_REDIS_REST_URL` - Redis URL for caching (optional)
- `UPSTASH_REDIS_REST_TOKEN` - Redis token for caching (optional)

## Local Development

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the application:
   ```bash
   python app.py
   ```

3. Open http://localhost:5000

## Requirements

- Python 3.8+
- Chrome/Chromium browser
- College portal access

## License

MIT License
