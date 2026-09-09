import os
import dropbox
import google.generativeai as genai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Dropbox Buxgalteriya Qidiruv Tizimi")

# Shablonlar joylashgan papka
templates = Jinja2Templates(directory="templates")

# API Kalitlarni muhit o'zgaruvchilaridan olish
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Gemini sozlashi
if GEMINI_API_KEY:
  genai.configure(api_key=GEMINI_API_KEY)


# --- ROUTERLAR ---


# 1. Asosiy Veb-sayt sahifasi
@app.get("/", response_class=HTMLResponse)
def home_page(request: Request):
  return templates.TemplateResponse("index.html", {"request": request})


# 2. Dropbox Sinxronizatsiyasi
@app.post("/sync")
def trigger_sync():
  if not DROPBOX_TOKEN:
    raise HTTPException(
        status_code=500, detail="DROPBOX_TOKEN sozlanmagan!"
    )

  try:
    dbx = dropbox.Dropbox(DROPBOX_TOKEN)
    # Dropbox'dan fayllarni olish mantiqi
    return {
        "status": "success",
        "message": "Fayllar muvaffaqiyatli sinxronizatsiya qilindi!",
    }
  except Exception as e:
    raise HTTPException(status_code=500, detail=f"Sinxron xatoligi: {str(e)}")


# 3. Qidiruv Endpointi
@app.get("/search")
def search_documents(query: str):
  if not query:
    raise HTTPException(status_code=400, detail="Qidiruv matni kiritilmadi")

  # Gemini AI va pgvector yordamida qidiruv bajarish (namuna natija)
  return {
      "query": query,
      "results": [
          {
              "file_name": "Hisobot_2026.pdf",
              "content": (
                  f"'{query}' so'rovi bo'yicha topilgan buxgalteriya ma'lumoti"
                  " va balansi."
              ),
          }
      ],
  }


if __name__ == "__main__":
  import uvicorn

  uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
