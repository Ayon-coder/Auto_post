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


def upload_for_instagram(file_stream, filename):
    """
    Upload to Cloudinary and return an Instagram-safe URL.

    Instagram requirements handled:
      • Aspect ratio must be between 4:5 (0.8) and 1.91:1
      • Maximum width 1080 px
      • JPEG format (PNG/WebP/BMP etc. converted automatically)
      • Images that are too tall or too wide are padded with an auto-detected
        background colour so no content is cropped.
    """
    try:
        upload_result = cloudinary.uploader.upload(
            file_stream,
            public_id=filename.split('.')[0],
            resource_type="auto"
        )

        url = upload_result.get("secure_url")
        res_type = upload_result.get("resource_type")
        width = upload_result.get("width", 0)
        height = upload_result.get("height", 0)

        # ── Image: apply Instagram-safe transformations via delivery URL ──
        if res_type == "image" and width and height:
            ratio = width / height

            # Step 1: scale down to 1080 px wide (never upscale)
            t1 = "c_limit,w_1080"

            # Step 2: fix aspect ratio if outside Instagram bounds + convert to JPEG
            if ratio < 0.8:
                # Image is too tall → pad to 4:5
                t2 = "ar_4:5,c_pad,b_auto,f_jpg,q_auto"
            elif ratio > 1.91:
                # Image is too wide → pad to 1.91:1
                t2 = "ar_1.91:1,c_pad,b_auto,f_jpg,q_auto"
            else:
                # Ratio is fine → just format-convert
                t2 = "f_jpg,q_auto"

            optimized_url = url.replace("/upload/", f"/upload/{t1}/{t2}/")
            return True, optimized_url, res_type, optimized_url

        # ── Video: return as-is (Buffer handles video encoding) ──
        thumbnail_url = None
        if res_type == "video":
            thumbnail_url = url.rsplit('.', 1)[0] + ".jpg"

        return True, url, res_type, thumbnail_url

    except Exception as e:
        return False, str(e), None, None


def _instagram_transform(url, width, height):
    """
    Apply Instagram-safe Cloudinary delivery transforms to an existing URL.
    No re-upload needed — just URL manipulation.
    """
    if not (width and height):
        return url
    ratio = width / height
    t1 = "c_limit,w_1080"
    if ratio < 0.8:
        t2 = "ar_4:5,c_pad,b_auto,f_jpg,q_auto"
    elif ratio > 1.91:
        t2 = "ar_1.91:1,c_pad,b_auto,f_jpg,q_auto"
    else:
        t2 = "f_jpg,q_auto"
    return url.replace("/upload/", f"/upload/{t1}/{t2}/")


def upload_once_with_variants(file_stream, filename):
    """
    Upload a file to Cloudinary ONCE and return both standard and
    Instagram-optimised URLs from the same upload.

    Returns dict:
        success, url, instagram_url, resource_type, thumbnail, instagram_thumbnail
    """
    try:
        upload_result = cloudinary.uploader.upload(
            file_stream,
            public_id=filename.split('.')[0],
            resource_type="auto",
        )

        url       = upload_result.get("secure_url")
        res_type  = upload_result.get("resource_type")
        width     = upload_result.get("width", 0)
        height    = upload_result.get("height", 0)

        # Standard thumbnail
        thumbnail = None
        if res_type == "video":
            thumbnail = url.rsplit('.', 1)[0] + ".jpg"

        # Instagram-safe delivery URL (same resource, different transforms)
        if res_type == "image" and width and height:
            insta_url = _instagram_transform(url, width, height)
            insta_thumb = insta_url
        else:
            insta_url = url
            insta_thumb = thumbnail

        return {
            "success": True,
            "url": url,
            "instagram_url": insta_url,
            "resource_type": res_type,
            "thumbnail": thumbnail,
            "instagram_thumbnail": insta_thumb,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": None,
            "instagram_url": None,
            "resource_type": None,
            "thumbnail": None,
            "instagram_thumbnail": None,
        }
