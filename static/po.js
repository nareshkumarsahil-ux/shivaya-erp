/* PCBFlow PO parser — ERP port (v3.86b). Global: PCBFlowPO.parsePoText(text, masters)
   RULES (kuch bhi invent nahi — PO me jo likha hai wahi):
   - Formal multi-item (Allied / Zoho table) + WhatsApp lines (LP-1837 20000pcs) dono
   - N printed S.No. = N lines; sparse serials (1,7,8,12) -> 12 slots pad (OCR-miss bhi row)
   - Same model twice = two lines (two job cards)
   - Models: 2-4 letter prefix + number (IIS / SCPL / OM / OSI / LP / SB / DHL...)
   - Qty x rate / GST mismatch = sirf WARN (hard fail nahi)
   - Na mile to field khaali (model/qty/rate/material...) — andaza kabhi nahi
   masters: [{id, model, party}] — EXACT normalized match (SCPL-19 vs SCPL-190 mix nahi),
   match hone par line.master_id + line.party (master ki party) set hota hai. */
(function () {
  function norm(s) {
    return String(s == null ? '' : s).toUpperCase().replace(/[^A-Z0-9]/g, '');
  }
  function guessPoNo(text) {
    text = String(text || '');
    var pats = [
      /\bPO\d{2}-\d{2,}(?:\/\d+)?\b/,                                  /* PO26-27366/6381 */
      /\bPO-\d{3,}\b/,                                                 /* PO-02002 */
      /\bP\.?O\.?\s*#\s*([A-Z0-9][A-Z0-9\-\/]*[A-Z0-9])/i,             /* P.O. # ABC123 */
      /Purchase Order\s*(?:No\.?|Number)?\s*[:#]\s*([A-Z0-9\-\/]*[A-Z][A-Z0-9\-\/]*)/i
    ];
    for (var i = 0; i < pats.length; i++) {
      var m = text.match(pats[i]);
      if (m) return String(m[1] || m[0]).toUpperCase();
    }
    return '';
  }
  function dateIso(s) {
    s = String(s || '').trim();
    var m = s.match(/(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/);
    if (m) {
      var d = +m[1], mo = +m[2], y = +m[3];
      if (y < 100) y += 2000;
      if (mo >= 1 && mo <= 12 && d >= 1 && d <= 31)
        return y + '-' + ('0' + mo).slice(-2) + '-' + ('0' + d).slice(-2);
    }
    m = s.match(/(\d{4})-(\d{2})-(\d{2})/);
    return m ? m[0] : '';
  }
  var MODEL_RE = /\b([A-Z]{2,4})\s*[-\u2013]?\s*([0-9][A-Z0-9\-_\/\\.]*)/gi;
  var NUM_RE = /\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{3,}(?:\.\d+)?|\d+(?:\.\d+)?/g;

  function numsIn(s) {
    var out = [], m, r = new RegExp(NUM_RE.source, 'g');
    while ((m = r.exec(s)) !== null) {
      var v = parseFloat(m[0].replace(/,/g, ''));
      if (isFinite(v) && v !== 0) out.push({ raw: m[0], v: v });
    }
    return out;
  }
  function qtyStr(v) {
    /* PCS = poorna number (5000.00 -> 5000) */
    return String(Math.floor(v));
  }

  var KNOWN = ['IIS', 'SCPL', 'OM', 'OSI', 'SB', 'DHL', 'LP'];
  var HEADER_RE = /^\s*(purchase order|tax invoice|proforma|description of goods|supplier|buyer|consignee|sr\.?\s*no|s\.?\s*no\.?\s*description|payment terms|terms of delivery|dispatch through|gst[in]|hsn|unit|quantity|rate\b|amount|delivery date|date\b|po no|p\.?o\.?\s*no|notes|reference)/i;

  function parseLine(raw, ctx) {
    var l = String(raw || '').replace(/\r/g, '').trim();
    if (!l) return null;
    if (HEADER_RE.test(l)) return null;                    /* table/header/date lines = data nahi */
    var sno = null, body = l;
    var ms = l.match(/^(\d{1,3})\s*[\.\)\-:]\s*(.+)$/) || l.match(/^(\d{1,3})\s+(.+)$/);
    if (ms) { sno = +ms[1]; body = ms[2].trim(); }
    /* model tokens: pehla = MODEL; doosra KNOWN-prefix = PARTY MODEL (kabhi copy nahi karte) */
    MODEL_RE.lastIndex = 0;
    var toks = [], tm;
    while ((tm = MODEL_RE.exec(body)) !== null) {
      var code = tm[1].toUpperCase() + '-' + tm[2];
      if (!toks.some(function (t) { return norm(t) === norm(code); })) toks.push(code);
    }
    var model = toks.length ? toks[0] : '';
    var party_model = '';
    for (var ti = 1; ti < toks.length; ti++) {
      var pf = toks[ti].split('-')[0];
      if (KNOWN.indexOf(pf) !== -1 && norm(toks[ti]) !== norm(model)) { party_model = toks[ti]; break; }
    }
    /* v4.04: model SPAN sahi se hatao ("RM 209" jaisa spaced token bhi) — warna 209 qty ban jata tha */
    var rest = body, mmatch = null;
    MODEL_RE.lastIndex = 0;
    mmatch = MODEL_RE.exec(body);
    if (mmatch) rest = body.slice(0, mmatch.index) + ' ' + body.slice(mmatch.index + mmatch[0].length);
    var warn = false, note = '';
    /* v4.04: parenthesis me party-codes/references hote hain — unke numbers qty/rate NAHI */
    rest = rest.replace(/\([^)]*\)/g, ' ');
    /* v4.04: spec-numbers (12W, 1.5MM, 5V...) qty/rate NAHI — pehle hi hatao */
    rest = rest.replace(/\b\d+(?:\.\d+)?\s*(?:w|kw|mm|cm|v|hz|amp|um|oz)\b\.?/gi, ' ');
    var desc = rest;
    var qm = rest.match(/(\d[\d,\.]*)\s*(?:pcs\.?|nos\.?|pc\b|pieces)/i);
    var qmv = qm ? parseFloat(qm[1].replace(/,/g, '')) : 0;
    var rm = rest.match(/(?:@|rs\.?|\u20B9)\s*(\d+(?:\.\d+)?)/i);
    var qty = '', rate = '';
    var nums = numsIn(rest.replace(/@|rs\.?|\u20B9/gi, ' ')).filter(function (n) { return n.v >= 0.5 && n.v < 1000000; });
    if (qm && qmv < 1000000) qty = qm[1].replace(/,/g, '');  /* pcs-suffix (chhota = sach ka qty) */
    else if (nums.length) {
      qty = qtyStr(nums[0].v);                               /* formal table: pehla number = qty */
      /* v4.04: rate = qty ke BAAD ka pehla decimal-number jo qty se alag ho
         (3.750/100.000/22.500). 13500/175000/85340000 jaise Taxable/HSN ints rate NAHI. */
      for (var ri = 1; ri < nums.length; ri++) {
        if (/\.\d{1,3}$/.test(String(nums[ri].raw)) && nums[ri].v !== nums[0].v) { rate = String(nums[ri].raw); break; }
      }
    }
    if (!qty) {
      var big = rest.match(/\b(\d{3,})\b/);
      qty = big ? big[1] : '';
    }
    if (rm) rate = rm[1];
    /* qty x rate vs taxable amount — mismatch sirf warning */
    if (qty && rate && nums.length > 2) {
      var val = parseFloat(qty) * parseFloat(rate);
      var tv = nums[nums.length - 1].v;
      if (isFinite(val) && isFinite(tv) && tv > 0 && Math.abs(val - tv) / tv > 0.02) warn = true;
    }
    /* material: description me se numbers/model nikal ke bacha hua text */
    var mat = desc
      .replace(new RegExp(model ? model.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&') : '\\u0000', 'i'), ' ')
      .replace(party_model ? new RegExp(party_model.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&'), 'i') : '\\u0000', ' ')
      .replace(/(thickness|thk)\s*[-:]?\s*\d+(\.\d+)?\s*mm/gi, ' ')
      .replace(/@|rs\.?|\u20B9/gi, ' ')
      .replace(/\b\d[\d,\.]*\b/g, ' ')
      .replace(/\s+/g, ' ').trim();
    if (!/[A-Za-z]{3}/.test(mat)) mat = '';
    if (/incomplete/i.test(l)) { note = 'scan incomplete'; warn = true; }
    if (sno != null && (!model || !qty)) warn = true;        /* serial hai par data adhoora */
    if (sno == null && (!model || (!qty && !rate))) return null;  /* header/noise (na data na serial) */
    return {
      sno: sno, model: model, party_model: party_model, qty: qty, rate: rate, material: mat,
      priority: /urgent/i.test(l) ? 'urgent' : (ctx.priority || 'normal'),
      po_no: ctx.po_no || '', party: '',
      order_date: ctx.order_date || '', due_date: '',
      warn: warn, note: note
    };
  }

  function parsePoText(text, masters) {
    text = String(text || '');
    masters = masters || [];
    var po_no = guessPoNo(text);
    var order_date = '', due_date = '', priority = /urgent/i.test(text) ? 'urgent' : 'normal';
    var party = '';
    var lines = text.split(/\r?\n/);
    lines.forEach(function (l) {
      if (!order_date && /^\s*Date\b/i.test(l)) order_date = dateIso(l);
      if (!due_date && /(delivery date|delivery\s*[:\-]|dispatch)/i.test(l)) due_date = dateIso(l);
      if (!party) {
        var m = l.match(/(?:buyer|party|consignee)(?:'s)?\s*name\s*:?\s*(.{3,60})/i);
        if (m && !/shivaya/i.test(m[1])) party = m[1].trim();
      }
    });
    var rows = [], snos = [];
    /* v4.04: scanned/table PO me ek item 4-5 physical lines me toota hota hai
       (description + (DHL-xxxx/IIS nnn) + THICKNESS-mm + numeric columns).
       Serial-lines >= 3 ho to table-mode: naya item sirf serial-line par,
       baaki lines pichhli row me judti hain. WhatsApp text (serial nahi) as-is. */
    var SER_RE = /^\s*(\d{1,3})\s*[\.\)]?\s+(?=[A-Z(])/;
    var serialCount = 0;
    lines.forEach(function (l) { if (SER_RE.test(l)) serialCount++; });
    if (serialCount >= 3) {
      var grouped = [];
      lines.forEach(function (l) {
        var t = String(l).trim();
        if (!t) return;
        if (SER_RE.test(t) || !grouped.length) grouped.push(t);
        else grouped[grouped.length - 1] += ' ' + t;
      });
      lines = grouped;
    }
    lines.forEach(function (l) {
      var r = parseLine(l, { po_no: po_no, priority: priority });
      if (r) { rows.push(r); if (r.sno) snos.push(r.sno); }
    });
    /* same serial do baar (model/qty alag lines) -> merge */
    var by = {}, merged = [];
    rows.forEach(function (r) {
      if (r.sno != null && by[r.sno] != null) {
        var k = by[r.sno];
        ['model', 'party_model', 'qty', 'rate', 'material'].forEach(function (f) {
          if (!k[f] && r[f]) k[f] = r[f];
        });
        return;
      }
      if (r.sno != null) by[r.sno] = r;
      merged.push(r);
    });
    rows = merged;
    /* "S.No. 12 - scan incomplete" hint bhi expected count batata hai */
    lines.forEach(function (l) {
      var mh = l.match(/s\.?\s*no\.?[^0-9]{0,6}(\d{1,3})/i);
      if (mh && (/incomplete/i.test(l) || /scan/i.test(l))) snos.push(+mh[1]);
    });
    /* sparse serials pad; bina-serial WA lines ko missing serials do (N lines = N cards) */
    if (snos.length) {
      var mx = Math.max.apply(null, snos), have = {}, missing = [];
      rows.forEach(function (r) { if (r.sno) have[r.sno] = 1; });
      for (var n = 1; n <= mx; n++) if (!have[n]) missing.push(n);
      rows.forEach(function (r) {
        if (r.sno == null && missing.length) r.sno = missing.shift();
      });
      missing.forEach(function (n) {
        rows.push({
          sno: n, model: '', party_model: '', qty: '', rate: '', material: '',
          priority: priority, po_no: po_no, party: '',
          order_date: order_date, due_date: due_date, warn: false,
          note: 'scan me nahi mila \u2014 khud bharo'
        });
      });
      rows.sort(function (a, b) {
        return ((b.sno == null) - (a.sno == null)) || ((a.sno || 0) - (b.sno || 0));
      });
    } else {
      rows.forEach(function (r, i) { if (r.sno == null) r.sno = i + 1; });
    }
    rows.forEach(function (r) {
      if (!r.order_date) r.order_date = order_date;
      if (!r.due_date) r.due_date = due_date;
      if (!r.po_no) r.po_no = po_no;
      /* masters: EXACT normalized match (SCPL-19 aur SCPL-190 mix nahi) */
      if (r.model) {
        var nm = norm(r.model);
        var hits = masters.filter(function (m) { return norm(m.model) === nm; });
        if (hits.length > 1 && r.party) {
          var bp = hits.filter(function (m) { return norm(m.party) === norm(r.party); });
          if (bp.length) hits = bp;
        }
        if (hits.length === 1) {
          r.master_id = hits[0].id;
          if (!r.party && hits[0].party) r.party = hits[0].party;
        } else if (hits.length > 1) {
          r.master_multi = hits.map(function (h) { return h.id; });
          r.warn = true;
        }
      }
    });
    /* v4.04: OCR ne bahut lines banayi ho to flag — UI warning dikhata hai */
    if (rows.length > 30) rows.tooMany = rows.length;
    return rows;
  }

  window.PCBFlowPO = { parsePoText: parsePoText, guessPoNo: guessPoNo, norm: norm };
})();
