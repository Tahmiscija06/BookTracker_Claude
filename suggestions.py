# Ova datoteka sadrži popis žanrova i manju REZERVNU listu knjiga.
# Rezervna lista se koristi kad nema interneta ili kad internet biblioteka
# ne vrati dovoljno prijedloga. Inače se prijedlozi traže u Open Library.
# Možete slobodno dodavati nove redove (isti format) ili brisati postojeće.

GENRES = [
    "Roman",
    "Klasik",
    "Fantastika",
    "Naučna fantastika",
    "Krimi / triler",
    "Historija",
    "Biografija",
    "Psihologija",
    "Poezija",
    "Razvoj ličnosti",
    "Ostalo",
]

# (naslov, autor, žanr, broj stranica)
SUGGESTIONS = [
    ("Na Drini ćuprija", "Ivo Andrić", "Klasik", 340),
    ("Prokleta avlija", "Ivo Andrić", "Klasik", 110),
    ("Travnička hronika", "Ivo Andrić", "Klasik", 480),
    ("Derviš i smrt", "Meša Selimović", "Klasik", 440),
    ("Tvrđava", "Meša Selimović", "Klasik", 400),
    ("Kameni spavač", "Mak Dizdar", "Poezija", 130),
    ("Sarajevski Marlboro", "Miljenko Jergović", "Roman", 200),
    ("Orlovi rano lete", "Branko Ćopić", "Roman", 170),
    ("1984", "George Orwell", "Klasik", 330),
    ("Životinjska farma", "George Orwell", "Klasik", 110),
    ("Ubiti pticu rugalicu", "Harper Lee", "Klasik", 340),
    ("Zločin i kazna", "Fjodor Dostojevski", "Klasik", 550),
    ("Stranac", "Albert Camus", "Klasik", 125),
    ("Stotinu godina samoće", "Gabriel García Márquez", "Roman", 420),
    ("Alkemičar", "Paulo Coelho", "Roman", 210),
    ("Mali princ", "Antoine de Saint-Exupéry", "Klasik", 96),
    ("Siddhartha", "Hermann Hesse", "Roman", 150),
    ("Norveška šuma", "Haruki Murakami", "Roman", 300),
    ("Hobit", "J. R. R. Tolkien", "Fantastika", 310),
    ("Čarobnjak iz Zemljomorja", "Ursula K. Le Guin", "Fantastika", 190),
    ("Dina", "Frank Herbert", "Naučna fantastika", 610),
    ("Zaklada", "Isaac Asimov", "Naučna fantastika", 255),
    ("Ubistvo u Orijent-ekspresu", "Agatha Christie", "Krimi / triler", 260),
    ("Nestala", "Gillian Flynn", "Krimi / triler", 430),
    ("Sapiens: Kratka historija čovječanstva", "Yuval Noah Harari", "Historija", 500),
    ("Dnevnik Ane Frank", "Anne Frank", "Biografija", 280),
    ("Brzo i sporo razmišljanje", "Daniel Kahneman", "Psihologija", 500),
    ("Čovjekova potraga za smislom", "Viktor Frankl", "Psihologija", 165),
    ("Atomske navike", "James Clear", "Razvoj ličnosti", 320),
]
