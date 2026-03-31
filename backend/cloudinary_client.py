import os
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

# Configuration
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

def upload_file_to_cloudinary(file_stream, filename):
    """
    Upload a file (Image, Video, or PDF) to Cloudinary.
    Returns (success_boolean, url_or_error, resource_type).
    """
    try:
        # Determine resource type based on extension
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        
        resource_type = "auto"
        if ext == 'pdf':
            resource_type = "raw" # PDFs are handled as raw or image depending on use case, but 'auto' is usually best
        elif ext in ['mp4', 'mov', 'avi', 'mkv']:
            resource_type = "video"
            
        upload_result = cloudinary.uploader.upload(
            file_stream,
            public_id=filename.split('.')[0],
            resource_type="auto" # 'auto' detects the type correctly
        )
        
        url = upload_result.get("secure_url")
        res_type = upload_result.get("resource_type")
        
        # For PDFs and Videos, Cloudinary can provide a thumbnail URL
        thumbnail_url = None
        if res_type == "video":
            # Generate a JPG thumbnail from the video
            thumbnail_url = url.rsplit('.', 1)[0] + ".jpg"
        elif ext == "pdf":
            # Generate a JPG thumbnail from the first page of the PDF
            thumbnail_url = url.replace("/upload/", "/upload/w_400,h_400,c_fill,pg_1/")
            thumbnail_url = thumbnail_url.rsplit('.', 1)[0] + ".jpg"

        return True, url, res_type, thumbnail_url
        
    except Exception as e:
        return False, str(e), None, None
