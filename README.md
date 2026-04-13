# InkCoverage.app

Free, open-source online ink coverage analyzer for prepress professionals.  
Upload a PDF, select a crop area, and get per-channel CMYK and spot color coverage percentages.

**Live at:** https://inkcoverage.app

---

## Tech Stack

| Layer      | Technology                          |
|------------|-------------------------------------|
| Backend    | Python / FastAPI                    |
| Frontend   | Vanilla HTML/CSS/JS (single page)   |
| Analysis   | Ghostscript `tiffsep` + Pillow      |
| Container  | Docker / Docker Compose             |
| Hosting    | Hetzner CAX11 (ARM64, 2 vCPU, 4 GB)|
| CDN/SSL    | Cloudflare (Free plan)              |
| Ads        | Google AdSense (pending setup)      |

## Server Details

- **IP:** 204.168.233.190
- **OS:** Ubuntu 24.04 (ARM64)
- **App directory:** `/opt/inkcoverage/`
- **Domain:** inkcoverage.app (DNS via Cloudflare)
- **HTTPS:** Cloudflare Flexible SSL

## Project Structure

```
web/
├── app.py                 # FastAPI backend (upload, preview, analyze)
├── Dockerfile             # Python 3.12 + Ghostscript
├── docker-compose.yml     # Container orchestration + resource limits
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable reference
├── .gitignore
├── .dockerignore
├── README.md              # This file
└── static/
    ├── index.html         # Main app (UI, crop tool, results panel)
    └── privacy.html       # Privacy policy page
```

## Features

- Upload PDF files (max 50 MB)
- Page-by-page preview rendering
- Interactive crop tool (mouse + touch)
- Per-channel CMYK ink coverage analysis
- Spot color detection and coverage
- Combined totals (CMYK, spot, grand total)
- Dimensions in mm and pt
- Rate limiting (30 analyses/hour per IP)
- Auto-cleanup of uploaded files (10 min TTL)
- Responsive design (desktop + mobile)
- SEO meta tags + structured data
- Google AdSense ad slot placeholders

## Deployment Commands

**Upload updated files from local PC:**
```bash
scp "C:/Claude/ColorPercentages/web/app.py" root@204.168.233.190:/opt/inkcoverage/app.py
scp "C:/Claude/ColorPercentages/web/static/index.html" root@204.168.233.190:/opt/inkcoverage/static/index.html
scp "C:/Claude/ColorPercentages/web/static/privacy.html" root@204.168.233.190:/opt/inkcoverage/static/privacy.html
```

**Rebuild and restart on server:**
```bash
ssh root@204.168.233.190 "cd /opt/inkcoverage && docker compose up -d --build"
```

**Check logs:**
```bash
ssh root@204.168.233.190 "cd /opt/inkcoverage && docker compose logs -f"
```

**Restart without rebuilding:**
```bash
ssh root@204.168.233.190 "cd /opt/inkcoverage && docker compose restart"
```

## Environment Variables

| Variable          | Default | Description                        |
|-------------------|---------|------------------------------------|
| MAX_UPLOAD_MB     | 50      | Maximum PDF upload size in MB      |
| ANALYSIS_DPI      | 72      | DPI for ink coverage analysis      |
| PREVIEW_DPI       | 150     | DPI for page preview rendering     |
| FILE_TTL_SECONDS  | 600     | Auto-delete uploaded files after   |
| RATE_LIMIT_MAX    | 30      | Max analyses per hour per IP       |
| GS_EXECUTABLE     | gs      | Path to Ghostscript binary         |

## Progress & Status

### Completed
- [x] FastAPI backend with upload, preview, and analyze endpoints
- [x] Frontend with crop tool, page navigation, results panel
- [x] Docker containerization
- [x] Hetzner CAX11 server provisioned (Nuremberg, ARM64)
- [x] App deployed and running at http://204.168.233.190:8000
- [x] Domain inkcoverage.app pointed to server via Cloudflare
- [x] HTTPS working via Cloudflare SSL
- [x] Privacy policy page added
- [x] Responsive mobile layout
- [x] SEO meta tags and structured data

### To Do
- [ ] Apply for Google AdSense and activate ad units
- [x] Replace GitHub link placeholders
- [x] Create GitHub repository and push source code
- [ ] Add favicon / app icon
- [ ] Add robots.txt and sitemap.xml
- [ ] Consider adding Terms of Service page
- [ ] Monitor server performance under real traffic
- [ ] Set up server backups (optional)

## License

AGPL-3.0 (required by Ghostscript AGPL dependency)
