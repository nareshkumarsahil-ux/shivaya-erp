# 🚀 Shivaya Circuit ERP — Vercel Par Deploy Guide

> 👉 **Sabse aasan step-by-step (Kab → Kya → Kaise): [GUIDE_DEPLOY_STEPS.md](GUIDE_DEPLOY_STEPS.md) kholo** —
> timeline, time estimates aur exact buttons ke saath. Neeche technical details hain.

**Goal:** ERP ko Vercel par host karo taaki **mobile aur PC dono par same data** dikhe —
kahin se bhi access karo, data ek hi jagah (cloud database) mein rahega.

> ⚠️ **Important:** Vercel serverless hai — wahan **SQLite file save nahi hoti** (filesystem temporary hota hai).
> Isliye ye project **Turso (free cloud SQLite)** support ke saath bana hai:
> - **Local:** `python3 app.py` → SQLite file (`circuit.db`) — sab kuch pehle jaisa.
> - **Vercel:** `TURSO_URL` + `TURSO_AUTH_TOKEN` env set karo → data Turso cloud par.
> - Env set na ho to app **khud local SQLite** use karti hai — koi code change nahi karna padta.

---

## STEP 1 — Turso Cloud Database (FREE) Banao

1. **Account banao:** https://turso.tech → Sign Up (GitHub/Google se ho jata hai)
2. **Turso CLI install karo** (PC par, ek baar):

   **Windows (PowerShell):**
   ```powershell
   winget install tursodatabase.turso
   ```
   **Mac/Linux:**
   ```bash
   curl -sSfL https://get.tur.so/install.sh | bash
   ```

3. **Login + DB banao:**
   ```bash
   turso auth login
   turso db create shivaya
   ```
4. **URL nikalo:**
   ```bash
   turso db show shivaya --url
   ```
   Output kuch aisa hoga: `libsql://shivaya-<your-name>.turso.io`  ← **ye TURSO_URL hai**

5. **Token banao (ye secret hai, kisi ko mat dena):**
   ```bash
   turso db tokens create shivaya
   ```
   Output: lambi string (`eyJhbGciOi...`)  ← **ye TURSO_AUTH_TOKEN hai**

---

## STEP 2 — Vercel Par Deploy Karo

1. https://vercel.com → Sign Up (GitHub se best)
2. **Add New → Project**
3. Project files import karo — do tarike:
   - **GitHub se:** project ko GitHub repo mein push kar do, phir Vercel us repo ko import kare.
   - **ZIP/Drag-drop:** `shivaya_circuit_vercel.zip` unzip karke folder ko Vercel par drag karo
     (Vercel CLI: `vercel deploy` — ya web par "Import" → folder select).

   > **ZIP wala sabse easy (bina GitHub):** Vercel dashboard → Add New → Project →
   > "Import Third-Party Git Repository" ki jagah neeche option hota hai ya phir
   > `npm i -g vercel` karke folder mein `vercel` command chalao.
   > Har tarike se same cheez deploy hoti hai: `api/index.py` + `vercel.json` + `requirements.txt`.

4. **Environment Variables set karo** (Vercel dashboard → Project → Settings → Environment Variables):
   | Name | Value |
   |---|---|
   | `TURSO_URL` | `libsql://shivaya-<your-name>.turso.io` |
   | `TURSO_AUTH_TOKEN` | (Step 1 ka token) |
   | `SECRET_KEY` | koi bhi random string, jaise `my-secret-2026-shivaya` |

5. **Deploy** dabao. 1-2 minute mein URL milega: `https://<project-name>.vercel.app`

6. URL kholo → **sahil / admin123** se login → mobile aur PC dono par same data ✅

**Pehli request par 2-4 second lag sakte hain (cold start) — normal hai, baad mein fast.**

---

## Data Same Kaise Rehta Hai?

- Har device (mobile/PC) usi Vercel URL par jaata hai.
- Sabki requests **Turso cloud DB** se connect hoti hain.
- Isliye job card, cut list, stock, BOM — sab kuch ek hi jagah, sab devices par same.

---

## Local Chalana (Development)

```bash
pip install -r requirements.txt
python3 app.py
# http://localhost:8000 → sahil / admin123
```
Local mein `circuit.db` SQLite file banti hai — Vercel wale data se alag (apni machine ka data).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| 500 error / DB error on Vercel | Vercel → Settings → Environment Variables check karo — `TURSO_URL` aur `TURSO_AUTH_TOKEN` exactly sahi hone chahiye. Phir **Redeploy** karo. |
| "Table not found" | Pehli request hi DB banati hai — page dobara refresh karo. |
| Turso token expire | `turso db tokens create shivaya` se naya token banao, Vercel env mein update karo, redeploy. |
| Data dikhna band | Turso free tier limit (500 databases, 9GB storage) — dashboard mein check karo. |

> Turso free tier is app ke liye kaafi hai (500 DBs, ~9GB, 1B rows read/month).
