"""Çevrimdışı testler: python3 -m unittest discover tests"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK))
import topla  # noqa: E402

FIX = KOK / "tests" / "fixtures"


class RG(unittest.TestCase):
    def test_yargi_bolumu_ve_ilan_atlama(self):
        k, muk = topla.rg_gun_ayristir((FIX / "rg_20260930.htm").read_text("utf-8"),
                                       dt.date(2026, 9, 30))
        self.assertEqual(len(k), 5)
        self.assertEqual(muk, [])
        turler = [x["tur"] for x in k]
        self.assertEqual(turler.count("Yönetmelik"), 2)
        self.assertEqual(turler.count("Tebliğ"), 1)
        self.assertEqual(turler.count("AYM Kararı"), 2)
        aym = [x for x in k if x["tur"] == "AYM Kararı"][0]
        self.assertEqual((aym["esasNo"], aym["kararNo"]), ("2025/156", "2026/131"))
        self.assertEqual(aym["url"], "https://www.resmigazete.gov.tr/eskiler/2026/09/20260930-4.pdf")
        self.assertEqual(aym["rgSayi"], "33386")
        self.assertTrue(all("Döviz" not in x["baslik"] for x in k))
        # üniversite yönetmelikleri düşük önemde
        self.assertTrue(all(x["onem"] == 1 for x in k if x["tur"] == "Yönetmelik"))

    def test_onem_ve_alan(self):
        k, _ = topla.rg_gun_ayristir((FIX / "rg_20261010.htm").read_text("utf-8"),
                                     dt.date(2026, 10, 10))
        by = {x["baslik"][:20]: x for x in k}
        self.assertEqual(len(k), 6)
        kam = [x for x in k if "Kamulaştır" in x["baslik"]][0]
        self.assertEqual(kam["onem"], 1)
        self.assertIn("Gayrimenkul", kam["alanlar"])
        sosyal = [x for x in k if "Sosyal Ağ" in x["baslik"]][0]
        self.assertEqual(sosyal["onem"], 2)
        self.assertIn("Bilişim", sosyal["alanlar"])
        ttk = [x for x in k if "6102" in x["baslik"]][0]
        self.assertIn("Ticaret", ttk["alanlar"])
        epdk = [x for x in k if "Enerji Piyasası" in x["baslik"]][0]
        self.assertEqual(epdk["tur"], "Kurul Kararı")
        self.assertEqual(len({x["id"] for x in k}), len(k))


class AYM(unittest.TestCase):
    def setUp(self):
        self.veri = json.loads((FIX / "aym_search.json").read_text("utf-8"))["data"]

    def test_donusum(self):
        k = [topla.aym_donustur(d) for d in self.veri]
        self.assertIsNone(k[0])  # kabul edilemezlik
        iri = k[1]
        self.assertEqual(iri["baslik"], "Tülay İri başvurusu")
        self.assertEqual(iri["onem"], 3)
        self.assertIn("Gayrimenkul", iri["alanlar"])
        self.assertTrue(iri["url"].endswith(
            "id=a2JiOjNkMjUxZDg4LWI5MTItNDBlOC04MmFjLTE2ZWE0ODU1NmJlOA&type=BireyselBasvuru"))
        self.assertIn("ön alım", iri["ozet"])
        self.assertEqual(k[3]["baslik"], "G.A. başvurusu")      # gizli ad korunur
        self.assertEqual(k[4]["onem"], 2)                       # ihlal olmadığı
        self.assertIn("Ceza", k[4]["alanlar"])
        norm = k[5]
        self.assertEqual(norm["tur"], "AYM Norm Denetimi")
        self.assertTrue(norm["baslik"].startswith("4721 sayılı Türk Medenî Kanunu"))
        self.assertIn("yürürlüğü ertelendi", norm["sonuc"])
        self.assertNotIn("İTİRAZIN KONUSU", norm["ozet"])
        self.assertIn("Aile", norm["alanlar"])
        self.assertIn("Ceza", k[6]["alanlar"])
        self.assertTrue(k[6]["baslik"].startswith("5271 sayılı Ceza Muhakemesi Kanunu"))


class Yorumlar(unittest.TestCase):
    def test_okuma_ve_hatalar(self):
        metin = """# açıklama
---
tarih: 2026-10-01
baslik: Deneme kararı
alan: Ceza, İş
yorum: Birinci satır
ikinci satır
---
tarih: 01.10.2026
baslik: Hatalı tarih
yorum: x
---
tarih: 2026-10-02
baslik: Yorumsuz
"""
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(metin)
        k, u = topla.yorumlari_oku(Path(f.name))
        self.assertEqual(len(k), 1)
        self.assertEqual(k[0]["ozet"], "Birinci satır ikinci satır")
        self.assertEqual(k[0]["alanlar"], ["Ceza", "İş"])
        self.assertEqual(len(u), 2)

    def test_gercek_dosya(self):
        k, u = topla.yorumlari_oku()
        self.assertEqual(u, [])
        self.assertEqual(len(k), 1)


class Butun(unittest.TestCase):
    def test_calistir_eslestirme_ve_koruma(self):
        rg_html = (FIX / "rg_20260930.htm").read_text("utf-8")
        aym_veri = json.loads((FIX / "aym_search.json").read_text("utf-8"))["data"]
        tmp = Path(tempfile.mkdtemp()) / "radar.json"
        eski_cikti, eski_bugun = topla.CIKTI, topla.bugun
        topla.CIKTI = tmp
        topla.bugun = lambda: dt.date(2026, 10, 12)
        try:
            def rg_ok():
                k, _ = topla.rg_gun_ayristir(rg_html, dt.date(2026, 9, 30))
                return k, {"ok": True, "gun": 1, "hata": None}

            def aym_ok():
                return [x for x in map(topla.aym_donustur, aym_veri) if x], {"ok": True, "hata": None}

            s = topla.calistir(rg_ok, aym_ok)
            ids = [k["id"] for k in s["kayitlar"]]
            self.assertEqual(len(ids), len(set(ids)))
            # RG'deki AYM kararı, AYM kaydıyla birleşti
            self.assertFalse(any(k["kaynak"] == "rg" and k["tur"] == "AYM Kararı"
                                 and k.get("esasNo") == "2025/156" for k in s["kayitlar"]))
            norm = [k for k in s["kayitlar"] if k.get("esasNo") == "2025/156"][0]
            self.assertTrue(norm["rgUrl"].endswith("20260930-4.pdf"))
            ilk = len(s["kayitlar"])

            # ertesi gün iki kaynak da çökerse eski kayıtlar kalmalı
            s2 = topla.calistir(lambda: ([], {"ok": False, "hata": ["x"]}),
                                lambda: ([], {"ok": False, "hata": ["y"]}))
            self.assertEqual(len(s2["kayitlar"]), ilk)
            self.assertFalse(s2["kaynaklar"]["rg"]["ok"])
        finally:
            topla.CIKTI, topla.bugun = eski_cikti, eski_bugun




class AYMSayfalama(unittest.TestCase):
    def test_bir_tabanli_ve_tekil(self):
        cagrilar = []

        def sahte(url, veri=None, json_mu=False, zaman_asimi=30, **_):
            b = json.loads(veri)
            cagrilar.append(b["page"])
            p = b["page"]
            ids = [f"{p}-{i}" for i in range(b["size"])] if p < 3 else ["2-0"]
            return json.dumps({"page": p, "data": [{"id": i} for i in ids]}).encode()

        eski = topla.getir
        topla.getir = sahte
        eski_uyku = topla.time.sleep
        topla.time.sleep = lambda s: None
        try:
            r = topla.aym_ara("BireyselBasvuru", 500)
        finally:
            topla.getir = eski
            topla.time.sleep = eski_uyku
        self.assertEqual(cagrilar[0], 1)
        self.assertEqual(len(r), len({x["id"] for x in r}))
        self.assertEqual(len(r), 80)


if __name__ == "__main__":
    unittest.main()
