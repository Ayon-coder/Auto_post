# AutoPost Dashboard

AutoPost is a streamlined dashboard for automating and managing your social media posts. It allows you to cross-post content and images to **LinkedIn** and **X (Twitter)** simultaneously using the Buffer API, with integrated image hosting via ImgBB.

## 🚀 Features

- **Multi-Platform Support**: Post to LinkedIn and X with a single click.
- **Image Uploads**: Integrated ImgBB support for hosting post images.
- **Responsive Dashboard**: A clean, modern web interface for creating posts.
- **Vercel Ready**: Pre-configured for easy deployment as a serverless application.
- **Parallel Dispatching**: Uses Python's `ThreadPoolExecutor` for fast, concurrent posting.

## 🛠️ Tech Stack

- **Backend**: Python (Flask)
- **Frontend**: HTML5, Vanilla JavaScript, CSS3
- **APIs**: Buffer (GraphQL), ImgBB
- **Deployment**: Vercel

## 📦 Installation

### 1. Clone the Repository
```bash
git clone <your-repo-url>
cd Auto_post
```

### 2. Set Up a Virtual Environment
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the root directory based on `.env.example`:

```ini
LINKEDIN_BUFFER_ACCESS_TOKEN=your_token_here
X_BUFFER_ACCESS_TOKEN=your_token_here
GRAPHQL_URL=https://api.buffer.com/graphql
IMGBB_API_KEY=your_imgbb_key_here
```

## 🏃 Running Locally

1. **Start the Backend**:
   ```bash
   # Windows (PowerShell)
   $env:PYTHONPATH = "backend"
   python backend/app.py
   ```

2. **Access the Dashboard**:
   Open [http://localhost:5000](http://localhost:5000) in your browser.

## ☁️ Deployment (Vercel)

The project includes a `vercel.json` for seamless deployment.

1. Install the Vercel CLI: `npm i -g vercel`
2. Run `vercel` in the project root.
3. Add your environment variables in the Vercel Dashboard (**Settings > Environment Variables**).

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
