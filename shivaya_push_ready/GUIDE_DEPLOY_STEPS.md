# 🚀 Shivaya Circuit ERP — Vercel Deploy Guide
### (Kab → Kya → Kaise — ek ek step, time ke saath)

> **Goal:** ERP ko internet par live karna taaki **mobile aur PC dono par SAME data** dikhe.
> **Kul time:** ~35-40 minute (ek baar karna hai, phir hamesha ke liye chalega).

---

## ⏱️ EK NAZAR MEIN — Poora Plan

| # | KAB | KYA KARNA HAI | KITNA TIME |
|---|---|---|---|
| 0 | Abhi | ZIP download + unzip karo | 2 min |
| 1 | Abhi | Turso cloud database banao (data ka ghar) | 10 min |
| 2 | Abhi | GitHub par code daalo | 10 min |
| 3 | Abhi | Vercel par deploy + 3 secret settings | 10 min |
| 4 | Phir | Pehla login + password change | 5 min |
| 5 | Last | Mobile par test — same data verify | 2 min |
| — | Roz | Kuch nahi karna — sab auto chalta hai | 0 min |
| — | Weekly (optional) | Data backup | 2 min |

---

## 📋 Pehle Ye Taiyar Rakho

- ✅ **PC** (Windows/Mac/Linux) + internet
- ✅ **Mobile** (test ke liye)
- ✅ **Email ID** (Turso + Vercel account ke liye) — ya GitHub account (sabse easy)
- ✅ **ZIP file:** `shivaya_circuit_vercel.zip` → download karke **unzip** kar lo
  → andar ek folder milega `shivaya_circuit/` — isi folder ki zaroorat hogi.

> 💡 **Yaad rakho:** Vercel par **SQLite file save nahi hoti** (Vercel serverless hai).
> Isliye data ke liye hum **Turso** use karte hain — ye FREE cloud database hai,
> bilkul SQLite jaisa. App mein maine ye support pehle se bana diya hai — aapko
> sirf URL aur Token daalna hai. **Code mein kuch nahi badalna.**

---

# STEP 1 — TURSO DATABASE BANAO (Data ka Ghar) 🏠

## Tarika A: Website se (sabse aasan — koi software install nahi) ✅ RECOMMENDED

1. Browser mein kholo: **https://app.turso.tech**
2. **Sign Up** karo (GitHub ya Google se 1 click mein ho jata hai)
3. **"Create Database"** button dabao
4. Naam do: `shivaya`
5. **Location (region) chuno: `Washington D.C. (iad)`** ⚡
   > ⚠️ **Kyun iad?** Vercel ka free plan app ko America (Washington, iad1) mein chalata hai.
   > Agar database Mumbai mein raha to har query ko America se Mumbai jaana padega
   > (300ms+ lagta hai — app slow lagta hai). DB bhi America (iad) mein hone se
   > queries 10ms mein chalti hain — **10-30x fast**. Mobile (India) se bhi
   > app tez chalega kyunki sirf page load ka ek hi chakkar America jata hai.
6. Create hone ke baad database kholo → **URL** dikhega kuch aisa:
   ```
   libsql://shivaya-<aapka-naam>.turso.io
   ```
   📝 **Ye URL copy karke kisi safe jagah likh lo** (Notepad/WhatsApp khud ko/paper)
7. Ab **Token** banao (ye password jaisa secret hai):
   - Database page mein **"Tokens" / "API Token"** section dhundho → **"Create Token"**
   - Database `shivaya` select karo → Create
   - Lambi string milegi (`eyJhbGciOi...` se shuru)
   📝 **Ye bhi copy karke safe jagah likh lo** — ye sirf ek baar dikhta hai!

> ⚠️ **TURSO_URL aur TURSO_AUTH_TOKEN dono note ho gaye?** Tabhi aage badho.
> Token kisi ko mat dena — isse koi bhi aapka data access kar sakta hai.

## Tarika B: Computer par command se (agar pasand ho)

```powershell
# Windows PowerShell (ek baar):
winget install tursodatabase.turso

# Mac/Linux:
curl -sSfL https://get.tur.so/install.sh | bash
```
Phir:
```bash
turso auth signup
turso db create shivaya --location iad
turso db show shivaya --url        # ← URL milega (note karo)
turso db tokens create shivaya     # ← Token milega (note karo)
```

---

# STEP 2 — CODE KO GITHUB PAR DAALO 📦

(Vercel ko code dene ke liye GitHub sabse asaan rasta hai.)

1. **https://github.com** → Sign Up/Login
2. Upar right mein **"+" → New repository**
   - Name: `shivaya-erp`
   - Public ya Private (Private better) → **Create repository**
3. Ab repo kholo → **"Add file" → "Upload files"**
4. **ZIP ke andar wale `shivaya_circuit` folder ko kholo** aur uske **ANDAR ki SAARI files** drag karke upload karo
   (folder khud nahi — andar ki files: `app.py`, `db.py`, `api/`, `templates/`, `vercel.json`, `requirements.txt`, ...)
5. Neeche **"Commit changes"** dabao ✅

> **Check:** repo mein `vercel.json` aur `api/index.py` dikhna chahiye (root par).

### (Alternate: Bina GitHub — Vercel CLI se)
```powershell
# Node.js install karo: https://nodejs.org (LTS version)
npm install -g vercel
cd shivaya_circuit
vercel login
vercel          # questions aayenge — sab default/Enter → deploy ho jayega
```

---

# STEP 3 — VERCEL PAR DEPLOY 🚀

1. **https://vercel.com** → **Sign Up** (GitHub se login karo — wahi account jo Step 2 mein use kiya)
2. **Add New → Project**
3. GitHub repos ki list mein **`shivaya-erp`** select karo → **Import**
4. Framework puchhe to **"Other"** chuno (hamara `vercel.json` already ready hai)
5. ⭐ **Environment Variables** section mein ye 3 variables daalo:

   | Name | Value |
   |---|---|
   | `TURSO_URL` | `libsql://shivaya-<aapka-naam>.turso.io` (Step 1 wala URL) |
   | `TURSO_AUTH_TOKEN` | Step 1 wala lamba token (`eyJ...`) |
   | `SECRET_KEY` | koi bhi random secret, jaise: `ShivayaCircuit@2026#Secure` |

6. **Deploy** dabao → 1-2 minute wait karo
7. Deploy complete hote hi URL milega: **`https://shivaya-erp-<naam>.vercel.app`**
   📝 Ye URL save karo — **yahi aapka ERP ka naya address hai** (mobile + PC dono ke liye)

> 💡 Agar bad mein env variables badle to Vercel khud **"Redeploy"** ka option dega —
> Redeploy karna zaroori hai tabhi nayi value lagti hai.

---

# STEP 4 — PEHLA LOGIN + PASSWORD CHANGE 🔐

1. URL kholo: `https://shivaya-erp-....vercel.app`
2. Login: username **`sahil`** · password **`admin123`**
3. ⚠️ **Pehli baar page 5-20 second le sakta hai** (ya "timeout" dikhe to refresh karo) —
   pehli request hi database ki tables banati hai. **Dusri baar se fast chalega.** Ye sirf ek baar hota hai.
4. Login ke baad sabse pehle: **Users & Access** → `sahil` → **Edit** → **password change kar lo**
   (default password sabko pata hota hai — change zaroori!)
5. Ab dashboard, job cards, cut list sab check karo — bilkul waisa hi hoga jaise preview mein tha.

---

# STEP 5 — MOBILE PAR TEST (Same Data Proof) 📱

1. Mobile browser (Chrome) mein wahi URL kholo
2. Login karo → **"Add to Home Screen"** kar do — bilkul app jaisa chalega 📲
3. **Test:** Mobile se koi naya order banao / job card mein kuch change karo
4. Ab **PC par refresh** karo → wahi change dikhna chahiye ✅
5. PC se change karo → mobile par refresh → dikhna chahiye ✅

> **Ye ho gaya = data ab cloud mein hai. Dono devices same database se judi hain.**

---

## 🗓️ ROZ KA USE — KAB KYA KARNA HAI

| Kab | Kya |
|---|---|
| **Har roz** | Kuch bhi nahi! Server 24×7 chalta hai, band nahi karna hota |
| Job card banana ho | Mobile ya PC — koi bhi device, same data |
| Operator ko dena ho | `ramesh / op123` ya `suresh / op123` login — wo bhi mobile se kar sakta hai |
| Naya user banana ho | **Users & Access** → add |
| Stock/BOM/cut list | Jahan bhi ho — sab sync hota hai |

## 🔄 APP UPDATE KARNA HO (naya feature aane par)

1. Naya code mujhse lo → files replace karo → GitHub par upload → Vercel **Redeploy**
2. **Data bilkul safe rahega** — data cloud DB mein hai, code se alag.

## 💾 BACKUP (weekly, optional — 2 min)

```bash
turso db shell shivaya ".dump" > backup.sql
```
Ye file kahin save rakho — kabhi bhi data wapas la sakte ho.
(Ya Turso dashboard → "Create backup/copy database" bhi kar sakte ho.)

---

## 🆘 PROBLEM AAYE TO — KYA KARNA

| Problem | Solution |
|---|---|
| Pehli baar page timeout / 500 | **Refresh karo** (2-3 baar try karo) — tables ban rahi hain |
| Login nahi ho raha | Username `sahil`, password wahi jo aapne change kiya |
| "Database error" aaye | Vercel → Settings → Environment Variables → `TURSO_URL` + `TURSO_AUTH_TOKEN` check karo → **Redeploy** |
| Data mobile par purana dikhe | Browser refresh (pull down) karo |
| Token kho gaya | Turso → database → **Create Token** (naya) → Vercel env update → Redeploy |
| App slow lag raha | Pehli request ke baad fast ho jata hai (cold start normal hai) |
| Vercel deploy fail | Repo mein `vercel.json` aur `api/index.py` root par hon — check Step 2 ka "Check" |

---

## ✅ FINAL CHECKLIST

- [ ] Turso: database `shivaya` ban gaya, **URL + Token note** ho gaye
- [ ] GitHub: saari files upload ho gayin
- [ ] Vercel: project imported, **3 env variables** set, Deploy success
- [ ] Login `sahil/admin123` → **password change** ho gaya
- [ ] Mobile par order banake PC par same dikh raha hai ✅

**Bas! Aapka ERP ab duniya mein kahin se bhi chalta hai — mobile ya PC, same data.** 🎉
