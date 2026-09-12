# Shivaya Circuit ERP — VS Code me kaise chalayein

## Step 1 — ZIP extract karo
- `shivaya_circuit_source.zip` download karo aur extract karo
- Andar `shivaya_circuit/` folder milega — **isi folder ko VS Code me kholo**

## Step 2 — VS Code me kholo
- VS Code → **File → Open Folder** → `shivaya_circuit` folder select karo

## Step 3 — Python + dependencies
- Python 3.9+ installed hona chahiye (https://python.org)
- VS Code me terminal kholo (`Ctrl + ~`) aur chalao:

```bash
pip install -r requirements.txt
```

## Step 4 — App chalao

```bash
python app.py
```

- Browser me kholo: **http://localhost:8000**

## Login details
| User | Username | Password |
|---|---|---|
| Admin (Sahil) | `sahil` | `admin123` |
| Har employee | pehla naam (jaise `ravi`, `shivam`) | `shivaya@123` |

## Note
- `circuit.db` is ZIP me already hai — aapka saara data (orders, dispatch, employees) saath aayega
- Naya database chahiye to `circuit.db` file delete kar do — app khud naya DB + seed data bana lega
- Vercel par deploy ke liye alag ZIP (`shivaya_circuit_vercel.zip`) + `GUIDE_DEPLOY_STEPS.md` use karo
