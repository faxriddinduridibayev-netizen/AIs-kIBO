import os
import dropbox
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

# FastAPI ilovasini yaratish
app = FastAPI(
    title="Dropbox Buxgalteriya Qidiruv Tizimi",
    description="Dropbox va Gemini AI yordamida buxgalteriya hujjatlarini sinxronizatsiya qilish va qidirish API tizimi.",
    version="0.1.0",
)

# Auth va Xavfsizlik sozlamalari
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Muhit o'zgaruvchilarini tekshirish
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")


# --- ROUTERLAR ---


# 1. Bosh sahifa (Not Found xatoligini oldini olish uchun)
@app.get("/", include_in_schema=False)
def root():
  """Bosh sahifaga kirilganda avtomatik /docs ga yo'naltiradi."""
  from fastapi.responses import RedirectResponse

  return RedirectResponse(url="/docs")


# 2. Avtorizatsiya (Login)
@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
  """Foydalanuvchini tekshirish va token qaytarish (Test uchun soddalashtirilgan)"""
  # Bu yerda o'zingizning login/parol mantiqingizni tekshirishingiz mumkin
  if form_data.username == "admin" and form_data.password == "admin123":
    return {"access_token": "fake-jwt-token-12345", "token_type": "bearer"}
  raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Login yoki parol noto'g'ri",
  )


# 3. Dropbox Sinxronizatsiyasi
@app.post("/sync")
def trigger_sync(token: str = Depends(oauth2_scheme)):
  """Dropbox'dagi fayllarni o'qib, bazaga (pgvector) saqlash bosqichi."""
  if not DROPBOX_TOKEN:
    raise HTTPException(
        status_code=500, detail="DROPBOX_TOKEN sozlanmagan!"
    )

  try:
    # Dropbox bilan bog'lanish va fayllarni olish mantiqi
    dbx = dropbox.Dropbox(DROPBOX_TOKEN)
    # Misol: files = dbx.files_list_folder('').entries
    return {
        "status": "success",
        "message": "Fayllar muvaffaqiyatli sinxronizatsiya qilindi.",
    }
  except Exception as e:
    raise HTTPException(status_code=500, detail=f"Sinxron xatoligi: {str(e)}")


# 4. Qidiruv Endpointi
@app.get("/search")
def search_documents(query: str, token: str = Depends(oauth2_scheme)):
  """Hujjatlar ichidan Gemini AI va pgvector yordamida qidirish."""
  if not query:
    raise HTTPException(status_code=400, detail="Qidiruv matni kiritilmadi")

  # Bu yerda Gemini Embedding yaratish va PostgreSQL pgvector'dan qidirish mantiqi bo'ladi
  return {
      "query": query,
      "results": [
          {
              "file_name": "Hisobot_2026.pdf",
              "content": "2026-yilgi buxgalteriya balansi ma'lumotlari...",
              "relevance_score": 0.95,
          }
      ],
  }


if __name__ == "__main__":
  import uvicorn

  uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
