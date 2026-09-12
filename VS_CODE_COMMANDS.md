# 💻 VS CODE COMMANDS — Copy-Paste Ready

> Har command ko **ek-ek karke** VS Code ke Terminal me paste karo aur Enter dabao.
> Terminal kholne ke liye: **`Ctrl` + `` ` ``** (backtick) ya menu: **Terminal → New Terminal**

---

## PART 1 — PROJECT VS CODE ME CHALAO ▶️

### Step 0: Folder kholo
- VS Code → **File → Open Folder** → `shivaya_circuit` (extract ki hui folder select karo)

### Step 1: Folder ke andar jao
```bash
cd shivaya_circuit
```
> 💡 Agar folder Desktop par hai aur terminal ne wo folder nahi khula, to poora path do:
> ```bash
> cd Desktop\shivaya_circuit
> ```

### Step 2: Dependencies install karo (pehli baar hi karna hai)
```bash
pip install -r requirements.txt
```

### Step 3: App chalao
```bash
python app.py
```
> ✅ Terminal me likha aayega: `Running on http://127.0.0.1:8000`
> Browser me kholo: **http://localhost:8000**

### Step 4: Band karna ho to
- Terminal me `Ctrl + C` dabao (app band ho jayega)

**Login:** `sahil / admin123` · Employee: pehla naam (jaise `ravi`) / `shivaya@123`

---

## PART 2 — GITHUB PAR UPLOAD KARO 📦 (VS Code Terminal se)

> Ye wahi commands hain jo pehle di thi — ab VS Code ke Terminal me paste karni hain.
> Pehle browser me **github.com → New Repository** banake naam rakho jaise `shivaya-erp`
> (README add mat karo — khali repo chahiye).

### Sabse pehle (sirf ek baar, machine par):
```bash
git config --global user.name "AAPKA NAAM"
git config --global user.email "aapki@email.com"
```

### Phir project folder me:
```bash
cd shivaya_circuit
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/AAPKA-USERNAME/shivaya-erp.git
git push -u origin main
```

> ⚠️ `AAPKA-USERNAME` ki jagah apna GitHub username likho.
> Push karte waqt GitHub ka login/password (Personal Access Token) maangega — browser se login ho jayega.

### Baad me naya code update karna ho (har baar ye 3 commands):
```bash
git add .
git commit -m "update"
git push
```

---

## PART 3 — CHHOTI CHEEZEIN 🧰

| Kaam | Command |
|---|---|
| Python version check | `python --version` |
| Pip check | `pip --version` |
| Naya fresh DB chahiye | `del circuit.db` (Windows) / `rm circuit.db` (Mac) — app khud naya banayega |
| App kisi aur port par | `python app.py 8001` |
| VS Code me Python extension | Extensions (Ctrl+Shift+X) → search `Python` → Install |

> 📌 **Vercel deploy** ke liye alag ZIP + `GUIDE_DEPLOY_STEPS.md` use karo — usme Turso + Vercel ka poora tarika hai.
