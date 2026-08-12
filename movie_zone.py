import os
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string

app = Flask(__name__)

MOVE2LINK_API_TOKEN = "16df4ded3bc0d3fa110af9eda3b38c91c2d14bce695b"

def convert_to_move2link(original_url):
    """Monetize original URL with Move2link API"""
    if not MOVE2LINK_API_TOKEN or not original_url:
        return original_url
    
    api_endpoint = f"https://move2link.com/api?api={MOVE2LINK_API_TOKEN}&url={original_url}"
    try:
        res = requests.get(api_endpoint, timeout=2).json()
        if res.get("status") == "success" and "shortenedUrl" in res:
            return res.get("shortenedUrl")
        elif "shortenedUrl" in res:
            return res.get("shortenedUrl")
    except Exception:
        pass
    return original_url

def fetch_extraflix_movies():
    target_url = "https://e5.extraflix.mobi/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    movies = []
    
    try:
        response = requests.get(target_url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        articles = soup.find_all('article')
        for art in articles[:24]: # Top 24 movies fetch karega
            title_tag = art.find('h2') or art.find('h3') or art.find('a')
            a_tag = art.find('a')
            img_tag = art.find('img')
            
            if title_tag and a_tag:
                title = title_tag.text.strip()
                raw_link = a_tag.get('href', '')
                
                poster = ""
                if img_tag:
                    poster = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-lazy-src') or ""
                if not poster or not poster.startswith('http'):
                    poster = "https://via.placeholder.com/200x300?text=Movie"
                
                category = "Bollywood"
                title_lower = title.lower()
                if "hollywood" in title_lower or "english" in title_lower or "dual" in title_lower:
                    category = "Hollywood"
                elif "season" in title_lower or "series" in title_lower or "s0" in title_lower:
                    category = "Web Series"

                # Direct Move2link Monetization
                monetized_link = convert_to_move2link(raw_link)

                movies.append({
                    "title": title,
                    "poster": poster,
                    "link": monetized_link,
                    "category": category
                })
    except Exception as e:
        print("Scraper Error:", e)
        
    return movies

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MOVIE ZONE</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Poppins', sans-serif; }
        body { background-color: #0b0f19; color: #ffffff; padding: 15px; }
        header { text-align: center; margin: 15px 0 25px 0; }
        header h1 { font-size: 1.8rem; color: #38bdf8; text-transform: uppercase; }
        
        .search-box { display: flex; justify-content: center; margin-bottom: 20px; }
        .search-box input { width: 100%; max-width: 450px; padding: 12px 18px; border-radius: 25px; border: 2px solid #1e293b; background: #151d30; color: #fff; outline: none; }

        .categories { display: flex; justify-content: center; gap: 8px; margin-bottom: 25px; flex-wrap: wrap; }
        .cat-btn { background: #1e293b; color: #cbd5e1; border: none; padding: 8px 16px; border-radius: 20px; cursor: pointer; font-weight: 600; }
        .cat-btn.active { background: #38bdf8; color: #0b0f19; }

        .movie-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 15px; max-width: 1000px; margin: 0 auto; }
        .movie-card { background: #151d30; border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid #1e293b; }
        .movie-card img { width: 100%; height: 220px; object-fit: cover; }
        .card-info { padding: 10px; text-align: center; }
        .card-info h3 { font-size: 13px; font-weight: 600; margin-bottom: 8px; height: 38px; overflow: hidden; }
        .download-btn { display: block; width: 100%; background: #22c55e; color: #fff; text-decoration: none; border: none; padding: 8px 0; border-radius: 6px; font-weight: 600; font-size: 12px; cursor: pointer; }
        .download-btn:disabled { background: #16803c; cursor: wait; }

        .progress-wrap { display: none; margin-top: 6px; }
        .progress-wrap.active { display: block; }
        .progress-track { width: 100%; height: 6px; background: #1e293b; border-radius: 4px; overflow: hidden; }
        .progress-fill { height: 100%; width: 0%; background: #38bdf8; transition: width 0.15s linear; }
        .progress-label { font-size: 10px; color: #94a3b8; margin-top: 3px; text-align: center; }
    </style>
</head>
<body>
    <header><h1>🎬 MOVIE ZONE</h1></header>
    <div class="search-box">
        <input type="text" id="searchInput" onkeyup="searchMovies()" placeholder="🔍 Type movie name...">
    </div>
    <div class="categories">
        <button class="cat-btn active" onclick="filterCategory('All', this)">All</button>
        <button class="cat-btn" onclick="filterCategory('Hollywood', this)">🍿 Hollywood</button>
        <button class="cat-btn" onclick="filterCategory('Bollywood', this)">🎬 Bollywood</button>
        <button class="cat-btn" onclick="filterCategory('Web Series', this)">📺 Web Series</button>
    </div>
    <div class="movie-grid" id="movieGrid">
        {% for movie in movies %}
        <div class="movie-card" data-title="{{ movie.title.lower() }}" data-category="{{ movie.category }}">
            <img src="{{ movie.poster }}" alt="Poster" onerror="this.src='https://via.placeholder.com/200x300?text=Poster';">
            <div class="card-info">
                <h3>{{ movie.title }}</h3>
                <button class="download-btn" onclick='downloadMovie(this, {{ movie.link|tojson }}, {{ movie.title|tojson }})'>📥 Download</button>
                <div class="progress-wrap">
                    <div class="progress-track"><div class="progress-fill"></div></div>
                    <div class="progress-label">0%</div>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
    <script>
        function searchMovies() {
            let input = document.getElementById('searchInput').value.toLowerCase();
            let cards = document.getElementsByClassName('movie-card');
            for (let card of cards) {
                let title = card.getAttribute('data-title');
                card.style.display = title.includes(input) ? "flex" : "none";
            }
        }
        async function downloadMovie(btn, url, title) {
            const card = btn.closest('.card-info');
            const wrap = card.querySelector('.progress-wrap');
            const fill = card.querySelector('.progress-fill');
            const label = card.querySelector('.progress-label');

            btn.disabled = true;
            btn.textContent = '⏳ Starting...';
            wrap.classList.add('active');
            fill.style.width = '0%';
            label.textContent = '0%';

            try {
                const res = await fetch(url);
                if (!res.ok || !res.body) throw new Error('bad response');

                const total = parseInt(res.headers.get('Content-Length') || '0', 10);
                const reader = res.body.getReader();
                const chunks = [];
                let received = 0;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    chunks.push(value);
                    received += value.length;
                    if (total) {
                        const pct = Math.min(100, Math.round((received / total) * 100));
                        fill.style.width = pct + '%';
                        label.textContent = pct + '%';
                        btn.textContent = '⏳ ' + pct + '%';
                    } else {
                        const mb = (received / (1024 * 1024)).toFixed(1);
                        label.textContent = mb + ' MB';
                        btn.textContent = '⏳ ' + mb + ' MB';
                    }
                }

                const blob = new Blob(chunks);
                const blobUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = blobUrl;
                a.download = title || 'movie';
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(blobUrl);

                fill.style.width = '100%';
                label.textContent = '✅ Done';
                btn.textContent = '✅ Downloaded';
            } catch (err) {
                // Likely blocked by CORS on the monetized/redirect link — fall back
                // to a normal navigation so the user still gets the file/ad-page.
                label.textContent = 'Opening in new tab...';
                btn.textContent = '📥 Download';
                window.open(url, '_blank');
            } finally {
                setTimeout(() => {
                    btn.disabled = false;
                    if (btn.textContent.indexOf('Downloaded') === -1) btn.textContent = '📥 Download';
                }, 1500);
            }
        }

        function filterCategory(cat, btn) {
            let buttons = document.getElementsByClassName('cat-btn');
            for (let b of buttons) b.classList.remove('active');
            btn.classList.add('active');
            let cards = document.getElementsByClassName('movie-card');
            for (let card of cards) {
                let cardCat = card.getAttribute('data-category');
                if (cat === 'All' || cardCat === cat) {
                    card.style.display = "flex";
                } else {
                    card.style.display = "none";
                }
            }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    movies = fetch_extraflix_movies()
    return render_template_string(HTML_TEMPLATE, movies=movies)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
