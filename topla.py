#!/usr/bin/env python3
"""
Nalia Hukuk — İçtihat Radarı veri toplayıcı.

Her gün GitHub Actions tarafından çalıştırılır ve şu kaynakları
data/radar.json dosyasında birleştirir:

  1. Resmî Gazete günlük fihristi  (resmigazete.gov.tr)
  2. Anayasa Mahkemesi Kararlar Bilgi Bankası
       - bireysel başvuru (yalnızca esas kararları)
       - norm denetimi
  3. Büro yorumları (yorumlar.txt)

Yalnızca Python standart kütüphanesi kullanılır; ek paket gerekmez.
Bir kaynak o gün erişilemezse önceki günlerden kalan kayıtlar korunur,
sayfa hiçbir zaman boş kalmaz.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

KOK = Path(__file__).resolve().parent
CIKTI = KOK / "data" / "radar.json"
YORUMLAR = KOK / "yorumlar.txt"

TR = dt.timezone(dt.timedelta(hours=3))
RG_GUN = 14          # geriye doğru taranacak Resmî Gazete günü
SAKLAMA_GUN = 60     # bu kadar günden eski RG/AYM kayıtları silinir
AYM_BB_ADET = 120    # taranacak son bireysel başvuru kararı
AYM_NORM_ADET = 30   # taranacak son norm denetimi kararı

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 NaliaHukukRadar/1.0")

AYM_API = "https://kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search"
AYM_KARAR_URL = ("https://kararlarbilgibankasi.anayasa.gov.tr/kbb/pages/search/"
                 "Tumu?id={kid}&type={tip}")


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

def bugun() -> dt.date:
    return dt.datetime.now(TR).date()


def getir(url: str, *, veri: bytes | None = None, json_mu=False,
          deneme=3, zaman_asimi=30) -> bytes:
    son_hata = None
    for i in range(deneme):
        try:
            basliklar = {"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"}
            if json_mu:
                basliklar["Content-Type"] = "application/json"
                basliklar["Accept"] = "application/json"
            istek = urllib.request.Request(url, data=veri, headers=basliklar)
            with urllib.request.urlopen(istek, timeout=zaman_asimi) as yanit:
                return yanit.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            son_hata = e
        except Exception as e:  # ağ hatası, zaman aşımı
            son_hata = e
        time.sleep(2 * (i + 1))
    raise son_hata  # type: ignore[misc]


def temiz(metin: str) -> str:
    metin = html.unescape(re.sub(r"<[^>]+>", " ", metin or ""))
    return re.sub(r"\s+", " ", metin).strip()


def tr_kucuk(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def tr_baslik(s: str) -> str:
    """'TÜLAY İRİ VE DİĞERLERİ' -> 'Tülay İri ve Diğerleri' (Türkçe İ/ı doğru)."""
    kucuk_kalan = {"ve", "ile"}
    out = []
    for w in tr_kucuk(s).split():
        if w in kucuk_kalan:
            out.append(w)
            continue
        ilk = w[0]
        ilk = "İ" if ilk == "i" else ("I" if ilk == "ı" else ilk.upper())
        out.append(ilk + w[1:])
    return " ".join(out)


# --------------------------------------------------------------------------
# Hukuk alanı etiketleme (başlık + özet üzerinden anahtar kelime)
# --------------------------------------------------------------------------

ALANLAR = {
    "Ceza": ["ceza muhakemesi", "türk ceza", "infaz", "hükümlü", "tutuklu",
             "tutukla", "ceza ve güvenlik", "cezaevi", "suç", "sanık", "müdafi",
             "kovuşturma", "soruşturma", "5271", "5237", "5275", "kolluk",
             "terörle mücadele", "kaçakçılık"],
    "Aile": ["türk medenî kanun", "türk medeni kanun", "boşanma", "nafaka",
             "velayet", "evlilik", "aile", "soybağı", "evlat edinme",
             "kadına karşı şiddet", "6284"],
    "İş": ["iş kanunu", "işçi", "işveren", "sosyal güvenlik", "sgk", "emekli",
           "kıdem", "5510", "4857", "sendika", "toplu iş", "asgari ücret",
           "iş sağlığı", "işsizlik", "fazla çalışma"],
    "İdare": ["idari yargılama", "2577", "devlet memurları", "657", "kamu görevli",
              "disiplin", "belediye", "idari para cezası", "kamu ihale",
              "ihale kanunu", "yükseköğretim", "üniversitesi", "akademik"],
    "Vergi": ["vergi", "harç", "gümrük", "katma değer", "gelir vergisi",
              "kurumlar vergisi", "213 sayılı", "6183", "amme alacak"],
    "Ticaret": ["türk ticaret", "6102", "anonim şirket", "limited şirket",
                "şirket", "sermaye piyasası", "bankacılık", "sigorta",
                "rekabet", "ticaret sicil", "kooperatif", "elektronik para",
                "ödeme hizmet"],
    "İcra": ["icra", "iflas", "2004 sayılı", "haciz", "konkordato",
             "ihtiyati"],
    "Gayrimenkul": ["tapu", "imar", "kamulaştırma", "kira", "kat mülkiyeti",
                    "taşınmaz", "ön alım", "ortaklığın giderilmesi", "kadastro",
                    "el atma"],
    "Borçlar": ["türk borçlar", "6098", "tazminat", "sözleşme", "haksız fiil",
                "alacak"],
    "Tüketici": ["tüketici", "6502", "yolcu hak", "mesafeli", "ayıplı"],
    "Bilişim": ["kişisel veri", "6698", "internet", "sosyal ağ", "elektronik haberleşme",
                "5651", "bilişim", "siber", "yaş doğrulama"],
    "Anayasa": ["anayasa mahkemesi", "bireysel başvuru", "ifade özgürlüğü",
                "adil yargılanma", "hakkaniyete uygun", "mülkiyet hakkı",
                "özel hayat", "kötü muamele", "yaşam hakkı", "toplantı ve gösteri",
                "din ve vicdan", "kişi hürriyeti"],
}


def alan_bul(*metinler: str) -> list[str]:
    birlesik = " " + tr_kucuk(" ".join(m for m in metinler if m)) + " "
    bulunan = []
    for alan, kelimeler in ALANLAR.items():
        for k in kelimeler:
            k2 = tr_kucuk(k)
            # kısa/sayısal anahtarlar kelime sınırıyla aranır
            if len(k2) <= 5:
                if re.search(r"(?<![0-9a-zçğıöşü])" + re.escape(k2) + r"(?![0-9a-zçğıöşü])", birlesik):
                    bulunan.append(alan)
                    break
            elif k2 in birlesik:
                bulunan.append(alan)
                break
    return bulunan


# --------------------------------------------------------------------------
# 1) Resmî Gazete
# --------------------------------------------------------------------------

class RGAyristirici(HTMLParser):
    """Günlük fihrist sayfasını (b>u başlıklar + "––" bağlantıları) okur."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.b = self.u = 0
        self.a_href: str | None = None
        self.a_metin: list[str] = []
        self.baslik_tampon: list[str] = []
        self.bolum = ""        # YÜRÜTME VE İDARE / YARGI / İLÂN
        self.alt_baslik = ""   # YÖNETMELİKLER, ANAYASA MAHKEMESİ KARARLARI ...
        self.kayitlar: list[dict] = []
        self.mukerrer: list[str] = []
        self.sayi: str | None = None
        self._ust_metin: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "b":
            self.b += 1
        elif tag == "u":
            self.u += 1
        elif tag == "a":
            self.a_href = dict(attrs).get("href") or ""
            self.a_metin = []

    def handle_endtag(self, tag):
        if tag == "u" and self.u:
            self.u -= 1
            if self.u == 0 and self.b:
                baslik = re.sub(r"\s+", " ", "".join(self.baslik_tampon)).strip()
                self.baslik_tampon = []
                if baslik:
                    self._baslik_isle(baslik)
        elif tag == "b" and self.b:
            self.b -= 1
        elif tag == "a" and self.a_href is not None:
            metin = re.sub(r"\s+", " ", "".join(self.a_metin)).strip()
            self._baglanti_isle(self.a_href, metin)
            self.a_href = None

    def handle_data(self, data):
        if self.a_href is not None:
            self.a_metin.append(data)
        if self.b and self.u:
            self.baslik_tampon.append(data)
        if self.sayi is None:
            self._ust_metin.append(data)
            m = re.search(r"Tarihli ve (\d+)\s*Sayılı", "".join(self._ust_metin))
            if m:
                self.sayi = m.group(1)

    def _baslik_isle(self, baslik: str):
        u = baslik.upper()
        if "BÖLÜMÜ" in u:
            self.bolum = u
            self.alt_baslik = ""
        else:
            self.alt_baslik = u

    def _baglanti_isle(self, href: str, metin: str):
        if re.search(r"\d{8}M\d+\.htm", href, re.I):
            self.mukerrer.append(href)
            return
        if "İLÂN" in self.bolum or "ILAN" in self.bolum or "İLAN" in self.bolum:
            return
        if not metin.startswith("–") and not metin.startswith("-"):
            return
        baslik = re.sub(r"^[–\-\s\u00a0]+", "", metin).strip()
        if not baslik or href.startswith("http") and "main.aspx" in href:
            return
        self.kayitlar.append({
            "baslik": baslik, "href": href,
            "bolum": self.bolum, "grup": self.alt_baslik,
        })


DUSUK_ONEM = re.compile(
    r"acele kamulaştır|sermayesinin|görevlendirilmesine|atama kararı|"
    r"insani yardım|faaliyet izninin iptal|kurulunun .* sayılı kararı|"
    r"üniversitesi .*yönetmeli|yüksekokulu|enstitüsü|meslek yüksekokulu|"
    r"merkezi yönetmeli", re.I)


def rg_tur(grup: str, bolum: str, baslik: str) -> tuple[str, int]:
    """Kayıt türü ve önem (3 yüksek, 2 orta, 1 düşük)."""
    g = grup.upper()
    b = tr_kucuk(baslik)
    if "ANAYASA MAHKEMES" in g:
        return "AYM Kararı", 3
    if "YARGITAY" in g:
        return "Yargıtay Kararı", 3
    if "DANIŞTAY" in g:
        return "Danıştay Kararı", 3
    if "UYUŞMAZLIK" in g:
        return "Uyuşmazlık Mah. Kararı", 3
    if "YARGI" in bolum and not g:
        return "Yargı Kararı", 3
    if g.startswith("KANUN") or "KANUNLAR" in g:
        return "Kanun", 3
    if "KARARNAME" in g:
        return "Cumhurbaşkanlığı Kararnamesi", 3
    if "GENELGE" in g:
        return "Genelge", 2
    if "YÖNETMEL" in g:
        return "Yönetmelik", 1 if DUSUK_ONEM.search(b) else 2
    if "TEBLİĞ" in g:
        return "Tebliğ", 1 if DUSUK_ONEM.search(b) else 2
    if "CUMHURBAŞKANI KARAR" in g:
        return "Cumhurbaşkanı Kararı", 1 if DUSUK_ONEM.search(b) else 2
    if "ATAMA" in g:
        return "Atama Kararı", 1
    if "KURUL" in g or "KURUM" in g:
        return "Kurul Kararı", 1
    if "MİLLETLERARASI" in g or "ANDLAŞMA" in g or "ANLAŞMA" in g:
        return "Milletlerarası Andlaşma", 2
    return (g.title() if g else "Düzenleme"), 1


def rg_gun_ayristir(icerik: str, tarih: dt.date) -> tuple[list[dict], list[str]]:
    p = RGAyristirici()
    p.feed(icerik)
    taban = f"https://www.resmigazete.gov.tr/eskiler/{tarih:%Y/%m}/"
    cikti = []
    for i, k in enumerate(p.kayitlar):
        tur, onem = rg_tur(k["grup"], k["bolum"], k["baslik"])
        url = k["href"] if k["href"].startswith("http") else taban + k["href"]
        kayit = {
            "id": f"rg-{tarih:%Y%m%d}-{re.sub(r'[^0-9A-Za-z-]', '', k['href']) or i}",
            "kaynak": "rg",
            "tur": tur,
            "baslik": k["baslik"],
            "ozet": "",
            "tarih": tarih.isoformat(),
            "url": url,
            "alanlar": alan_bul(k["baslik"]),
            "onem": onem,
            "rgSayi": p.sayi,
        }
        m = re.search(r"E:\s*(\d{4}/\d+),\s*K:\s*(\d{4}/\d+)", k["baslik"])
        if m and tur == "AYM Kararı":
            kayit["esasNo"], kayit["kararNo"] = m.group(1), m.group(2)
        m2 = re.search(r"(\d{4}/\d+) Başvuru Numaralı", k["baslik"])
        if m2 and tur == "AYM Kararı":
            kayit["basvuruNo"] = m2.group(1)
        cikti.append(kayit)
    return cikti, p.mukerrer


def rg_topla(gunler: int = RG_GUN) -> tuple[list[dict], dict]:
    kayitlar, hatalar, basarili = [], [], 0
    for g in range(gunler):
        tarih = bugun() - dt.timedelta(days=g)
        url = f"https://www.resmigazete.gov.tr/eskiler/{tarih:%Y/%m/%Y%m%d}.htm"
        try:
            ham = getir(url).decode("windows-1254", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # o gün henüz yayımlanmamış / yok
            hatalar.append(f"{tarih}: HTTP {e.code}")
            continue
        except Exception as e:
            hatalar.append(f"{tarih}: {e}")
            continue
        if "Resmî Gazete" not in ham and "Resmi Gazete" not in ham:
            continue
        basarili += 1
        gun_kayitlari, mukerrerler = rg_gun_ayristir(ham, tarih)
        kayitlar += gun_kayitlari
        for m in mukerrerler:
            try:
                murl = m if m.startswith("http") else \
                    f"https://www.resmigazete.gov.tr/eskiler/{tarih:%Y/%m}/{m}"
                mham = getir(murl).decode("windows-1254", errors="replace")
                mk, _ = rg_gun_ayristir(mham, tarih)
                for k in mk:
                    k["id"] += "-m"
                    k["tur"] += " (Mükerrer)"
                kayitlar += mk
            except Exception as e:
                hatalar.append(f"{tarih} mükerrer: {e}")
        time.sleep(0.5)
    durum = {"ok": basarili > 0, "gun": basarili,
             "hata": hatalar[:5] if hatalar else None}
    return kayitlar, durum


# --------------------------------------------------------------------------
# 2) Anayasa Mahkemesi
# --------------------------------------------------------------------------

def aym_baglanti(kid: str, tip: str) -> str:
    kod = base64.b64encode(f"kbb:{kid}".encode()).decode().rstrip("=")
    return AYM_KARAR_URL.format(kid=kod, tip=tip)


def aym_ozet(ham: str) -> str:
    metin = temiz(ham)
    metin = re.sub(r"^(İTİRAZIN|DAVANIN|BAŞVURUNUN) KONUSU\s*:\s*", "", metin)
    return metin


def aym_donustur(d: dict) -> dict | None:
    tip = d.get("kararTipi")
    if tip == "BireyselBasvuru":
        sonuc = d.get("kararTuruBasvuruSonucuLabel") or ""
        if not sonuc.startswith("Esas"):
            return None  # kabul edilemezlik kararları radarı doldurmasın
        ad = (d.get("basvuruAdi") or "").strip()
        gizli = d.get("gizlilik") or re.fullmatch(r"([A-ZÇĞİÖŞÜ]\.\s*)+", ad)
        baslik = f"{ad if gizli else tr_baslik(ad)} başvurusu"
        tur, onem = "AYM Bireysel Başvuru", (3 if "İhlal)" in sonuc and "Olmadığı" not in sonuc else 2)
        no = d.get("basvuruNo")
        etiket = f"B. No: {no}"
    elif tip == "NormDenetimi":
        sonuc = d.get("kararTuruDosyaSonucuLabel") or ""
        tur, onem = "AYM Norm Denetimi", 3
        etiket = f"E. {d.get('esasNo')}, K. {d.get('kararNo')}"
        baslik = None
    else:
        return None

    ozet = aym_ozet(d.get("kararKonusu") or "")
    if baslik is None:
        m = re.search(r"(\d+ sayılı .*?(?:Kanunu|Kanun|Kararnamesi|Kararname|"
                      r"İçtüzüğü|Yönetmeliği))\s*(?:[’'`]\s*n?[ıiuü]n)?\s*"
                      r"(?:((?:geçici |ek )?\d+)\. madde)?", ozet)
        if m:
            baslik = m.group(1).strip()
            if m.group(2):
                no = m.group(2)
                baslik += (f" geçici m.{no.split()[1]}" if no.startswith("geçici")
                           else f" ek m.{no.split()[1]}" if no.startswith("ek ")
                           else f" m.{no}")
        else:
            baslik = "Norm denetimi kararı"
        if d.get("kararYururlugundeErteleme"):
            sonuc += " (yürürlüğü ertelendi)"
    tarih = (d.get("resmiGazeteTarihi") or (d.get("yayinTarihi") or "")[:10]
             or d.get("kararTarihi"))
    return {
        "id": f"aym-{d['id']}",
        "kaynak": "aym",
        "tur": tur,
        "baslik": baslik,
        "ozet": ozet,
        "sonuc": sonuc,
        "etiket": etiket,
        "tarih": tarih,
        "kararTarihi": d.get("kararTarihi"),
        "url": aym_baglanti(d["id"], tip),
        "alanlar": alan_bul(ozet) or ["Anayasa"],
        "onem": onem,
        "esasNo": d.get("esasNo"),
        "kararNo": d.get("kararNo"),
        "basvuruNo": d.get("basvuruNo"),
    }


def aym_ara(tip: str, adet: int) -> list[dict]:
    # Not: servis sayfaları 1'den başlatır (page=0 ile page=1 aynı sonucu verir).
    sonuc, goruldu, sayfa, boy = [], set(), 1, 40
    while len(sonuc) < adet and sayfa <= 20:
        govde = json.dumps({"page": sayfa, "size": boy, "kararTipi": tip,
                            "sort": "yayinTarihi", "order": "desc"}).encode()
        j = json.loads(getir(AYM_API, veri=govde, json_mu=True, zaman_asimi=60))
        veri = j.get("data") or []
        for d in veri:
            if d.get("id") not in goruldu:
                goruldu.add(d.get("id"))
                sonuc.append(d)
        if len(veri) < boy:
            break
        sayfa += 1
        time.sleep(1)
    return sonuc[:adet]


def aym_topla() -> tuple[list[dict], dict]:
    kayitlar, hatalar = [], []
    for tip, adet in (("BireyselBasvuru", AYM_BB_ADET), ("NormDenetimi", AYM_NORM_ADET)):
        try:
            for d in aym_ara(tip, adet):
                k = aym_donustur(d)
                if k:
                    kayitlar.append(k)
        except Exception as e:
            hatalar.append(f"{tip}: {e}")
    return kayitlar, {"ok": not hatalar or bool(kayitlar), "hata": hatalar or None}


# --------------------------------------------------------------------------
# 3) Büro yorumları
# --------------------------------------------------------------------------

def yorumlari_oku(yol: Path = YORUMLAR) -> tuple[list[dict], list[str]]:
    if not yol.exists():
        return [], []
    kayitlar, uyarilar = [], []
    bloklar = re.split(r"^-{3,}\s*$", yol.read_text(encoding="utf-8"), flags=re.M)
    for no, blok in enumerate(bloklar[1:], 1):
        alanlar, yorum_satirlari, yorumda = {}, [], False
        for satir in blok.splitlines():
            if satir.strip().startswith("#"):
                continue
            m = re.match(r"^\s*(tarih|baslik|başlık|kaynak|alan|yazar|yorum)\s*:\s*(.*)$",
                         satir, re.I)
            if m and not (yorumda and m.group(1).lower() not in
                          ("tarih", "baslik", "başlık", "kaynak", "alan", "yazar")):
                anahtar = m.group(1).lower().replace("başlık", "baslik")
                if anahtar == "yorum":
                    yorumda = True
                    if m.group(2).strip():
                        yorum_satirlari.append(m.group(2).strip())
                else:
                    yorumda = False
                    alanlar[anahtar] = m.group(2).strip()
            elif yorumda:
                yorum_satirlari.append(satir.strip())
        yorum = re.sub(r"\s+", " ", " ".join(yorum_satirlari)).strip()
        if not any([alanlar, yorum]):
            continue
        try:
            tarih = dt.date.fromisoformat(alanlar.get("tarih", "")).isoformat()
        except ValueError:
            uyarilar.append(f"{no}. kayıt: tarih YYYY-AA-GG biçiminde olmalı")
            continue
        if not alanlar.get("baslik") or not yorum:
            uyarilar.append(f"{no}. kayıt: baslik ve yorum zorunlu")
            continue
        etiketler = [a.strip() for a in alanlar.get("alan", "").split(",") if a.strip()]
        kayitlar.append({
            "id": f"buro-{tarih}-{no}",
            "kaynak": "buro",
            "tur": "Büro notu",
            "baslik": alanlar["baslik"],
            "ozet": yorum,
            "yazar": alanlar.get("yazar") or "Nalia Hukuk",
            "tarih": tarih,
            "url": alanlar.get("kaynak") or None,
            "alanlar": etiketler or alan_bul(alanlar["baslik"], yorum),
            "onem": 3,
        })
    return kayitlar, uyarilar


# --------------------------------------------------------------------------
# Birleştirme
# --------------------------------------------------------------------------

def birlestir(onceki: list[dict], yeni: list[dict], kaynak: str,
              yenilendi: bool) -> list[dict]:
    """Kaynak bugün başarılıysa yeni kayıtlar esas alınır; eski kayıtlar
    (taranan pencerenin dışında kalanlar) korunur."""
    if not yenilendi:
        return [k for k in onceki if k.get("kaynak") == kaynak]
    yeni_idler = {k["id"] for k in yeni}
    eski = [k for k in onceki if k.get("kaynak") == kaynak and k["id"] not in yeni_idler]
    return yeni + eski


def aym_rg_eslestir(kayitlar: list[dict]) -> list[dict]:
    """Resmî Gazete'deki AYM kararı ile AYM bilgi bankasındaki aynı kararı
    tek kayıtta toplar (özet AYM'den, RG bağlantısı ek olarak)."""
    aym = {}
    for k in kayitlar:
        if k["kaynak"] != "aym":
            continue
        if k.get("esasNo"):
            aym[("n", k["esasNo"], k.get("kararNo"))] = k
        if k.get("basvuruNo"):
            aym[("b", k["basvuruNo"])] = k
    cikti = []
    for k in kayitlar:
        anahtar = (("n", k["esasNo"], k.get("kararNo")) if k.get("esasNo")
                   else ("b", k["basvuruNo"]) if k.get("basvuruNo") else None)
        if k["kaynak"] == "rg" and anahtar:
            es = aym.get(anahtar)
            if es:
                es["rgUrl"] = k["url"]
                es["rgSayi"] = k.get("rgSayi")
                es["tarih"] = k["tarih"]
                continue
        cikti.append(k)
    return cikti


def calistir(rg=rg_topla, aym=aym_topla) -> dict:
    onceki = []
    if CIKTI.exists():
        try:
            onceki = json.loads(CIKTI.read_text(encoding="utf-8")).get("kayitlar", [])
        except Exception:
            onceki = []

    rg_k, rg_d = rg()
    aym_k, aym_d = aym()
    buro_k, uyarilar = yorumlari_oku()

    sinir = (bugun() - dt.timedelta(days=SAKLAMA_GUN)).isoformat()
    tum = (birlestir(onceki, rg_k, "rg", rg_d["ok"]) +
           birlestir(onceki, aym_k, "aym", aym_d["ok"]))
    tum = [k for k in tum if (k.get("tarih") or "") >= sinir]
    tum = aym_rg_eslestir(tum) + buro_k

    # aynı kimlik iki kez gelmesin
    goruldu, tekil = set(), []
    for k in tum:
        if k["id"] in goruldu:
            continue
        goruldu.add(k["id"])
        tekil.append(k)
    tekil.sort(key=lambda k: (k.get("tarih") or "", k.get("onem", 0)), reverse=True)

    sonuc = {
        "surum": 1,
        "guncelleme": dt.datetime.now(TR).isoformat(timespec="minutes"),
        "kaynaklar": {"rg": rg_d, "aym": aym_d,
                      "buro": {"ok": not uyarilar, "uyari": uyarilar or None}},
        "kayitlar": tekil,
    }
    CIKTI.parent.mkdir(parents=True, exist_ok=True)
    CIKTI.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    return sonuc


if __name__ == "__main__":
    s = calistir()
    say = {}
    for k in s["kayitlar"]:
        say[k["kaynak"]] = say.get(k["kaynak"], 0) + 1
    print("Güncellendi:", s["guncelleme"], "| kayıt:", say)
    for ad, d in s["kaynaklar"].items():
        if d.get("hata") or d.get("uyari"):
            print(f"UYARI [{ad}]:", d.get("hata") or d.get("uyari"))
    # RG ve AYM'nin ikisi de başarısızsa iş akışı hata versin (e-posta bildirimi gelir)
    if not s["kaynaklar"]["rg"]["ok"] and not s["kaynaklar"]["aym"]["ok"]:
        sys.exit(1)
