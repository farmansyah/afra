"""Ready-made outlines for coursework (tugas kuliah). Each new document starts with the usual structure of an
Indonesian campus assignment and exports with the "tugas" Word format (cover, Kata Pengantar, Daftar Isi, BAB I...)."""

STARTERS = {
    "makalah": {
        "name": "Makalah", "doc_type": "MAKALAH",
        "content": """# Judul Makalah

## Kata Pengantar

Puji syukur ... (ucapan terima kasih kepada dosen pengampu dan pihak yang membantu).

## Pendahuluan

### Latar Belakang

Uraikan konteks topik dan mengapa topik ini penting, didukung sumber [@kunci].

### Rumusan Masalah

1. ...
2. ...

### Tujuan

1. ...
2. ...

## Pembahasan

### Subtopik 1

### Subtopik 2

## Penutup

### Kesimpulan

### Saran

## Daftar Pustaka
""",
    },
    "praktikum": {
        "name": "Laporan Praktikum", "doc_type": "LAPORAN PRAKTIKUM",
        "content": """# Judul Percobaan

## Pendahuluan

### Latar Belakang

### Tujuan Praktikum

## Tinjauan Pustaka

Teori dasar yang mendukung percobaan, dengan sitasi [@kunci].

## Metodologi

### Alat dan Bahan

| Alat | Bahan |
|---|---|
| ... | ... |

### Prosedur Kerja

1. ...
2. ...

## Hasil dan Pembahasan

### Hasil Pengamatan

| Perlakuan | Hasil |
|---|---|
| ... | ... |

### Pembahasan

## Penutup

### Kesimpulan

### Saran

## Daftar Pustaka

## Lampiran
""",
    },
    "proposal": {
        "name": "Proposal Penelitian", "doc_type": "PROPOSAL PENELITIAN",
        "content": """# Judul Penelitian

## Pendahuluan

### Latar Belakang

### Rumusan Masalah

### Tujuan Penelitian

### Manfaat Penelitian

### Batasan Masalah

## Tinjauan Pustaka

### Landasan Teori

### Penelitian Terdahulu

| Peneliti (Tahun) | Metode | Hasil |
|---|---|---|
| [@kunci] | ... | ... |

### Kerangka Pemikiran

### Hipotesis

## Metodologi Penelitian

### Jenis dan Desain Penelitian

### Waktu dan Tempat

### Alat dan Bahan / Populasi dan Sampel

### Prosedur Penelitian

### Analisis Data

### Jadwal Penelitian

## Daftar Pustaka
""",
    },
    "resume": {
        "name": "Resume / Review Jurnal", "doc_type": "RESUME JURNAL",
        "content": """# Resume Jurnal: Judul Artikel

## Identitas Jurnal

| | |
|---|---|
| Judul artikel | ... |
| Penulis | ... |
| Nama jurnal, volume, tahun | ... |
| DOI | ... |

## Ringkasan

### Latar Belakang dan Tujuan

### Metode

### Hasil Utama

### Kesimpulan Penulis

## Analisis

### Kelebihan

### Kekurangan

### Relevansi dengan Mata Kuliah

## Daftar Pustaka
""",
    },
}


def meta(key: str) -> dict:
    s = STARTERS[key]
    return {"template": "tugas", "doc_type": s["doc_type"], "lang": "id", "starter": key}
