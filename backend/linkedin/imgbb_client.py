import os
import requests
from dotenv import load_dotenv

load_dotenv()

IMGBB_API_KEY = os.getenv("IMGBB_API_KEY") or os.getenv("ImgBB_API_KEY")

def upload_image_to_imgbb(file_stream, filename):
    """
    Upload an image file stream to ImgBB and return the public URL.
    Returns (success_boolean, url_or_error_message).
    """
    if not IMGBB_API_KEY:
        return False, "IMGBB_API_KEY not found in environment variables. Please add it to your .env file."
        
    url = "https://api.imgbb.com/1/upload"
    payload = {
        "key": IMGBB_API_KEY,
        "name": filename
    }
    
    # ImgBB expects the file in the 'image' field
    files = {
        "image": file_stream
    }
    
    try:
        response = requests.post(url, data=payload, files=files, timeout=120)
        data = response.json()
        
        if response.status_code == 200 and data.get("success"):
            return True, data["data"]["url"]
        else:
            return False, data.get("error", {}).get("message", "Unknown error uploading to ImgBB")
            
    except requests.RequestException as e:
        return False, f"Network error uploading image: {str(e)}"
