
# My AI v2

Fitur:
- Gemini sebagai otak utama
- Streaming response
- Login/register
- Free / Premium / Owner
- Owner panel di /admin
- Auto Web: routing otomatis antara Gemini dan You.com berdasarkan kebutuhan pertanyaan
- Upload gambar/dokumen untuk Premium/Owner
- Chat history per sesi
- API key hanya di backend .env

Catatan keamanan:
Owner tidak mempunyai mode untuk menonaktifkan safeguard model/provider atau membuat aplikasi membantu tindakan berbahaya/ilegal. Owner dapat mengelola pengguna, tier, dan fitur aplikasi.

Setup:
1. Salin .env.example menjadi .env
2. Isi GEMINI_API_KEY dan YDC_API_KEY
3. Set OWNER_USERNAME dan OWNER_PASSWORD
4. Install: py -m pip install -r requirements.txt
5. Jalankan: py app.py
6. Buka http://127.0.0.1:5000
7. Account/Login tersedia langsung dari sidebar. Owner panel: http://127.0.0.1:5000/admin

Model dapat diganti lewat GEMINI_MODEL di .env bila key lu punya akses ke model lain.
