import hashlib
import hmac
import os
import sqlite3
import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

APP_TITLE = "Библиотека — учёт книг"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")
ROLE_NAMES = {"admin": "администратор", "librarian": "библиотекарь"}
GENRES = ["Роман", "Повесть", "Рассказы", "Фантастика", "Фэнтези", "Детектив", "Классика",
          "Поэзия", "Научпоп", "История", "Антиутопия", "Приключения"]


class LibraryError(Exception):
    """Ошибка бизнес-логики: текст показывается пользователю."""


#проверка данных
def validate_book(title, author, year, genre, total):
    """Проверяет поля формы книги и возвращает (title, author, year:int, genre, total:int)."""
    title, author, genre = " ".join(title.split()), " ".join(author.split()), " ".join(genre.split())
    if not title:
        raise LibraryError("Укажите название книги")
    if not author:
        raise LibraryError("Укажите автора")
    if not genre:
        raise LibraryError("Укажите жанр")
    year_text, total_text = year.strip(), total.strip()
    if not year_text.isdigit():
        raise LibraryError("Год издания должен быть целым числом")
    max_year = date.today().year + 1
    if not 1 <= int(year_text) <= max_year:
        raise LibraryError(f"Год издания должен быть от 1 до {max_year}")
    if not total_text.lstrip("-").isdigit():
        raise LibraryError("Количество экземпляров должно быть целым числом")
    if int(total_text) < 0:
        raise LibraryError("Количество экземпляров не может быть меньше нуля")
    return title, author, int(year_text), genre, int(total_text)


def fmt_date(iso):
    """2026-11-24 -> 24.11.2026"""
    if not iso:
        return "—"
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def _hash(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 100_000).hex()


#слой данных
class Database:
    BOOK_SELECT = """
        SELECT b.id, b.title, b.author, b.year, b.genre, b.total,
               b.total - (SELECT COUNT(*) FROM loans l WHERE l.book_id = b.id AND l.returned_at IS NULL) AS available
        FROM books b"""

    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self._seed()

    def _create_schema(self):
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'librarian')),
                    full_name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    genre TEXT NOT NULL,
                    total INTEGER NOT NULL CHECK (total >= 0));
                CREATE TABLE IF NOT EXISTS loans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                    reader TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    returned_at TEXT);""")

    def _seed(self):
        if not self.conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            self.add_user("admin", "admin123", "admin", "Администратор")
            self.add_user("librarian", "lib123", "librarian", "Библиотекарь")
        if not self.conn.execute("SELECT 1 FROM books LIMIT 1").fetchone():
            demo = [
                ("Война и мир", "Лев Толстой", 1869, "Роман", 3),
                ("Преступление и наказание", "Фёдор Достоевский", 1866, "Роман", 2),
                ("Мастер и Маргарита", "Михаил Булгаков", 1967, "Роман", 4),
                ("Евгений Онегин", "Александр Пушкин", 1833, "Поэзия", 2),
                ("Отцы и дети", "Иван Тургенев", 1862, "Роман", 2),
                ("1984", "Джордж Оруэлл", 1949, "Антиутопия", 3),
                ("Скотный двор", "Джордж Оруэлл", 1945, "Антиутопия", 2),
                ("451 градус по Фаренгейту", "Рэй Брэдбери", 1953, "Фантастика", 2),
                ("Гарри Поттер и философский камень", "Джоан Роулинг", 1997, "Фэнтези", 5),
                ("Властелин колец", "Дж. Р. Р. Толкин", 1954, "Фэнтези", 3),
                ("Убийство в «Восточном экспрессе»", "Агата Кристи", 1934, "Детектив", 2),
                ("Краткая история времени", "Стивен Хокинг", 1988, "Научпоп", 1),
            ]
            with self.conn:
                self.conn.executemany("INSERT INTO books(title, author, year, genre, total) VALUES (?,?,?,?,?)", demo)
            self.issue_book(1, "Айбек Нурланов")
            self.issue_book(6, "Мария Иванова")

    #пользователи
    def add_user(self, login, password, role, full_name):
        salt = os.urandom(16).hex()
        with self.conn:
            self.conn.execute("INSERT INTO users(login, password_hash, salt, role, full_name) VALUES (?,?,?,?,?)",
                              (login, _hash(password, salt), salt, role, full_name))

    def authenticate(self, login, password):
        row = self.conn.execute("SELECT * FROM users WHERE login = ?", (login.strip(),)).fetchone()
        if row and hmac.compare_digest(row["password_hash"], _hash(password, row["salt"])):
            return dict(row)
        return None

    #книги
    def list_books(self, query="", field="all"):
        """Поиск по части слова без учёта регистра (в т.ч. для кириллицы — делаем в Python, т.к. SQLite LIKE ASCII-only)."""
        rows = self.conn.execute(self.BOOK_SELECT + " ORDER BY b.id").fetchall()
        q = query.strip().casefold()
        if not q:
            return rows
        keys = ("title", "author", "genre") if field == "all" else (field,)
        return [r for r in rows if any(q in str(r[k]).casefold() for k in keys)]

    def get_book(self, book_id):
        return self.conn.execute(self.BOOK_SELECT + " WHERE b.id = ?", (book_id,)).fetchone()

    def add_book(self, title, author, year, genre, total):
        with self.conn:
            cur = self.conn.execute("INSERT INTO books(title, author, year, genre, total) VALUES (?,?,?,?,?)",
                                    (title, author, year, genre, total))
        return cur.lastrowid

    def update_book(self, book_id, title, author, year, genre, total):
        book = self.get_book(book_id)
        if book is None:
            raise LibraryError("Книга не найдена (возможно, уже удалена)")
        issued = book["total"] - book["available"]
        if total < issued:
            raise LibraryError(f"Нельзя указать меньше {issued}: столько экземпляров сейчас на руках")
        with self.conn:
            self.conn.execute("UPDATE books SET title=?, author=?, year=?, genre=?, total=? WHERE id=?",
                              (title, author, year, genre, total, book_id))

    def delete_book(self, book_id):
        book = self.get_book(book_id)
        if book is None:
            raise LibraryError("Книга не найдена (возможно, уже удалена)")
        issued = book["total"] - book["available"]
        if issued:
            raise LibraryError(f"Нельзя удалить: {issued} экз. сейчас на руках. Сначала примите возврат")
        with self.conn:
            self.conn.execute("DELETE FROM books WHERE id = ?", (book_id,))

    #выдача и возврат
    def issue_book(self, book_id, reader):
        reader = " ".join(reader.split())
        if not reader:
            raise LibraryError("Укажите имя читателя")
        book = self.get_book(book_id)
        if book is None:
            raise LibraryError("Книга не найдена")
        if book["available"] <= 0:
            raise LibraryError(f"Нет доступных экземпляров книги «{book['title']}»")
        with self.conn:
            self.conn.execute("INSERT INTO loans(book_id, reader, issued_at) VALUES (?,?,?)",
                              (book_id, reader, date.today().isoformat()))

    def return_book(self, loan_id):
        loan = self.conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if loan is None:
            raise LibraryError("Запись о выдаче не найдена")
        if loan["returned_at"] is not None:
            raise LibraryError("Эта книга уже возвращена")
        with self.conn:
            self.conn.execute("UPDATE loans SET returned_at = ? WHERE id = ?", (date.today().isoformat(), loan_id))

    def list_loans(self, active_only=False):
        sql = """SELECT l.id, b.title, b.author, l.reader, l.issued_at, l.returned_at
                 FROM loans l JOIN books b ON b.id = l.book_id"""
        if active_only:
            sql += " WHERE l.returned_at IS NULL"
        # невозвращённые сверху, затем новые выдачи
        return self.conn.execute(sql + " ORDER BY (l.returned_at IS NOT NULL), l.issued_at DESC, l.id DESC").fetchall()


# общие элементы интерфейса
def center(win, width, height):
    win.update_idletasks()
    x = (win.winfo_screenwidth() - width) // 2
    y = max((win.winfo_screenheight() - height) // 3, 0)
    win.geometry(f"{width}x{height}+{x}+{y}")


def make_tree(parent, columns, height=14):
    """columns: [(key, заголовок, ширина, выравнивание)]. Возвращает (рамка, Treeview)."""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="browse", height=height)
    for key, head, width, anchor in columns:
        tree.heading(key, text=head)
        tree.column(key, width=width, anchor=anchor, stretch=key in ("title", "author", "reader"))
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    tree.tag_configure("out", foreground="#b00020")
    tree.tag_configure("done", foreground="#6b6b6b")
    return frame, tree


BOOK_COLUMNS = [("num", "№", 50, "center"), ("title", "Название", 260, "w"), ("author", "Автор", 180, "w"),
                ("year", "Год", 60, "center"), ("genre", "Жанр", 110, "w"),
                ("total", "Всего", 70, "center"), ("available", "Доступно", 80, "center")]


def fill_books(tree, books):
    tree.delete(*tree.get_children())
    for n, b in enumerate(books, 1):
        tree.insert("", "end", iid=str(b["id"]),
                    values=(n, b["title"], b["author"], b["year"], b["genre"], b["total"], b["available"]),
                    tags=("out",) if b["available"] == 0 else ())


def selected_id(tree):
    sel = tree.selection()
    return int(sel[0]) if sel else None


def title_label(parent, text):
    return ttk.Label(parent, text=text, font=("Segoe UI", 14, "bold"))


#форма книги (добавление и редактирование)
class BookForm(ttk.Frame):
    FIELDS = [("Название книги *", "title"), ("Автор *", "author"), ("Год издания *", "year"),
              ("Жанр *", "genre"), ("Количество экземпляров *", "total")]

    def __init__(self, parent, db, on_saved, book=None, on_cancel=None):
        super().__init__(parent, padding=16)
        self.db, self.on_saved, self.book, self.on_cancel = db, on_saved, book, on_cancel
        self.vars = {key: tk.StringVar() for _, key in self.FIELDS}
        for i, (label, key) in enumerate(self.FIELDS):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", pady=6, padx=(0, 12))
            if key == "genre":
                widget = ttk.Combobox(self, textvariable=self.vars[key], values=GENRES, width=38)
            else:
                widget = ttk.Entry(self, textvariable=self.vars[key], width=40)
            widget.grid(row=i, column=1, sticky="ew")
            if i == 0:
                self.first = widget
        self.columnconfigure(1, weight=1)
        buttons = ttk.Frame(self)
        buttons.grid(row=len(self.FIELDS), column=0, columnspan=2, pady=(16, 0), sticky="e")
        ttk.Button(buttons, text="Сохранить", command=self.save).pack(side="left", padx=4)
        if on_cancel:
            ttk.Button(buttons, text="Отмена", command=on_cancel).pack(side="left", padx=4)
        else:
            ttk.Button(buttons, text="Очистить", command=self.clear).pack(side="left", padx=4)
        if book is not None:
            for key in self.vars:
                self.vars[key].set(str(book[key]))

    def clear(self):
        for var in self.vars.values():
            var.set("")
        self.first.focus_set()

    def save(self):
        parent = self.winfo_toplevel()
        try:
            data = validate_book(*(self.vars[key].get() for _, key in self.FIELDS))
            if self.book is not None:
                self.db.update_book(self.book["id"], *data)
            else:
                self.db.add_book(*data)
        except LibraryError as e:
            messagebox.showerror("Ошибка", str(e), parent=parent)
            return
        if self.book is None:
            messagebox.showinfo("Готово", "Книга добавлена", parent=parent)
            self.clear()
        self.on_saved()


#разделы главного окна
class AddBookFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=20)
        title_label(self, "Добавить книгу").pack(anchor="w")
        ttk.Label(self, text="Поля, отмеченные *, обязательны.").pack(anchor="w", pady=(0, 8))
        BookForm(self, app.db, app.refresh_all).pack(anchor="nw")

    def refresh(self):
        pass


class BooksFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=20)
        self.app = app
        title_label(self, "Список книг").pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        self.edit_btn = ttk.Button(bar, text="Изменить", command=self.edit, state="disabled")
        self.del_btn = ttk.Button(bar, text="Удалить", command=self.delete, state="disabled")
        self.edit_btn.pack(side="left")
        self.del_btn.pack(side="left", padx=8)
        if app.user["role"] != "admin":
            ttk.Label(bar, text="Изменять и удалять книги может только администратор.").pack(side="left", padx=8)
        frame, self.tree = make_tree(self, BOOK_COLUMNS)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    def on_select(self, _event=None):
        state = "normal" if self.app.user["role"] == "admin" and selected_id(self.tree) is not None else "disabled"
        self.edit_btn.config(state=state)
        self.del_btn.config(state=state)

    def refresh(self):
        fill_books(self.tree, self.app.db.list_books())
        self.on_select()

    def edit(self):
        book_id = selected_id(self.tree)
        if book_id is None:
            messagebox.showwarning("Нет выбора", "Сначала выберите книгу в таблице")
            return
        book = self.app.db.get_book(book_id)
        if book is None:
            self.app.refresh_all()
            return
        dialog = tk.Toplevel(self.app)
        dialog.title("Редактирование книги")
        dialog.transient(self.app)
        dialog.resizable(False, False)

        def saved():
            dialog.destroy()
            self.app.refresh_all()

        BookForm(dialog, self.app.db, saved, book=book, on_cancel=dialog.destroy).pack()
        center(dialog, 520, 330)
        dialog.grab_set()

    def delete(self):
        book_id = selected_id(self.tree)
        if book_id is None:
            messagebox.showwarning("Нет выбора", "Сначала выберите книгу в таблице")
            return
        book = self.app.db.get_book(book_id)
        if book is None:
            self.app.refresh_all()
            return
        if not messagebox.askyesno("Подтверждение", f"Удалить книгу «{book['title']}»?"):
            return
        try:
            self.app.db.delete_book(book_id)
        except LibraryError as e:
            messagebox.showerror("Ошибка", str(e))
            return
        self.app.refresh_all()


class SearchFrame(ttk.Frame):
    FIELDS = {"Везде": "all", "Название": "title", "Автор": "author", "Жанр": "genre"}

    def __init__(self, parent, app):
        super().__init__(parent, padding=20)
        self.app = app
        title_label(self, "Поиск").pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.query = tk.StringVar()
        self.field = tk.StringVar(value="Везде")
        entry = ttk.Entry(bar, textvariable=self.query, width=40)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.search())
        ttk.Combobox(bar, textvariable=self.field, values=list(self.FIELDS), state="readonly", width=10).pack(side="left", padx=8)
        ttk.Button(bar, text="Найти", command=self.search).pack(side="left")
        ttk.Button(bar, text="Сбросить", command=self.reset).pack(side="left", padx=8)
        self.status = ttk.Label(self, text="")
        self.status.pack(anchor="w", pady=6)
        frame, self.tree = make_tree(self, BOOK_COLUMNS)
        frame.pack(fill="both", expand=True)
        self.query.trace_add("write", lambda *_: self.search())   # поиск «на лету»
        self.field.trace_add("write", lambda *_: self.search())

    def reset(self):
        self.query.set("")

    def search(self):
        books = self.app.db.list_books(self.query.get(), self.FIELDS[self.field.get()])
        fill_books(self.tree, books)
        if not self.query.get().strip():
            self.status.config(text=f"Показаны все книги: {len(books)}. Введите часть названия, автора или жанра.")
        elif books:
            self.status.config(text=f"Найдено: {len(books)}")
        else:
            self.status.config(text="По вашему запросу ничего не найдено. Проверьте написание или попробуйте часть слова.")

    def refresh(self):
        self.search()


class IssueFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=20)
        self.app = app
        title_label(self, "Выдать книгу").pack(anchor="w", pady=(0, 8))
        form = ttk.Frame(self)
        form.pack(fill="x")
        ttk.Label(form, text="Имя читателя *").pack(side="left")
        self.reader = tk.StringVar()
        entry = ttk.Entry(form, textvariable=self.reader, width=34)
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda _e: self.issue())
        ttk.Button(form, text="Выдать", command=self.issue).pack(side="left")
        self.date_label = ttk.Label(self, text="")
        self.date_label.pack(anchor="w", pady=6)
        frame, self.tree = make_tree(self, BOOK_COLUMNS)
        frame.pack(fill="both", expand=True)

    def refresh(self):
        fill_books(self.tree, self.app.db.list_books())
        self.date_label.config(text=f"Выберите книгу в таблице. Дата выдачи: {fmt_date(date.today().isoformat())} (сегодня)")

    def issue(self):
        book_id = selected_id(self.tree)
        if book_id is None:
            messagebox.showwarning("Нет выбора", "Сначала выберите книгу в таблице")
            return
        try:
            self.app.db.issue_book(book_id, self.reader.get())
        except LibraryError as e:
            messagebox.showerror("Ошибка", str(e))
            return
        self.reader.set("")
        self.app.refresh_all()
        messagebox.showinfo("Готово", "Книга выдана")


LOAN_COLUMNS = [("num", "№", 50, "center"), ("title", "Название", 250, "w"), ("author", "Автор", 170, "w"),
                ("reader", "Читатель", 170, "w"), ("issued", "Дата выдачи", 100, "center"),
                ("returned", "Возвращена", 100, "center")]


def fill_loans(tree, loans):
    tree.delete(*tree.get_children())
    for n, row in enumerate(loans, 1):
        tree.insert("", "end", iid=str(row["id"]),
                    values=(n, row["title"], row["author"], row["reader"], fmt_date(row["issued_at"]),
                            fmt_date(row["returned_at"]) if row["returned_at"] else "на руках"),
                    tags=("done",) if row["returned_at"] else ())


class ReturnFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=20)
        self.app = app
        title_label(self, "Вернуть книгу").pack(anchor="w")
        ttk.Label(self, text="Выберите выданную книгу и нажмите «Принять возврат».").pack(anchor="w", pady=(0, 8))
        ttk.Button(self, text="Принять возврат", command=self.give_back).pack(side="bottom", anchor="w", pady=(10, 0))
        frame, self.tree = make_tree(self, LOAN_COLUMNS[:-1])
        frame.pack(fill="both", expand=True)

    def refresh(self):
        fill_loans(self.tree, self.app.db.list_loans(active_only=True))

    def give_back(self):
        loan_id = selected_id(self.tree)
        if loan_id is None:
            messagebox.showwarning("Нет выбора", "Сначала выберите выданную книгу в таблице")
            return
        try:
            self.app.db.return_book(loan_id)
        except LibraryError as e:
            messagebox.showerror("Ошибка", str(e))
            self.app.refresh_all()
            return
        self.app.refresh_all()
        messagebox.showinfo("Готово", "Книга возвращена")


class LoansFrame(ttk.Frame):
    """Отдельный список выданных книг: кто, что и когда взял; возвращённые отмечены серым."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=20)
        self.app = app
        title_label(self, "Выданные книги").pack(anchor="w", pady=(0, 8))
        self.only_active = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text="Только не возвращённые", variable=self.only_active, command=self.refresh).pack(anchor="w")
        self.status = ttk.Label(self, text="")
        self.status.pack(anchor="w", pady=6)
        frame, self.tree = make_tree(self, LOAN_COLUMNS)
        frame.pack(fill="both", expand=True)

    def refresh(self):
        loans = self.app.db.list_loans(active_only=self.only_active.get())
        fill_loans(self.tree, loans)
        self.status.config(text=f"Записей: {len(loans)}" if loans else "Выданных книг нет.")


#окна
class LoginWindow(tk.Tk):
    def __init__(self, db):
        super().__init__()
        self.db, self.user = db, None
        self.title(f"{APP_TITLE} — вход")
        self.resizable(False, False)
        box = ttk.Frame(self, padding=24)
        box.pack()
        ttk.Label(box, text="Вход в систему", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 14))
        ttk.Label(box, text="Логин").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Label(box, text="Пароль").grid(row=2, column=0, sticky="w", pady=6)
        self.login = tk.StringVar()
        self.password = tk.StringVar()
        self.login_entry = ttk.Entry(box, textvariable=self.login, width=26)
        self.login_entry.grid(row=1, column=1, padx=(12, 0))
        pass_entry = ttk.Entry(box, textvariable=self.password, width=26, show="•")   # пароль скрыт
        pass_entry.grid(row=2, column=1, padx=(12, 0))
        buttons = ttk.Frame(box)
        buttons.grid(row=3, column=0, columnspan=2, pady=(16, 0))
        ttk.Button(buttons, text="Войти", command=self.submit).pack(side="left", padx=6)
        ttk.Button(buttons, text="Выход", command=self.destroy).pack(side="left", padx=6)
        ttk.Label(box, text="Демо: admin / admin123,  librarian / lib123", foreground="#6b6b6b").grid(
            row=4, column=0, columnspan=2, pady=(14, 0))
        self.bind("<Return>", lambda _e: self.submit())
        center(self, 380, 300)
        self.login_entry.focus_set()

    def submit(self):
        user = self.db.authenticate(self.login.get(), self.password.get())
        if user is None:
            messagebox.showerror("Ошибка входа", "Неверный логин или пароль", parent=self)
            self.password.set("")
            return
        self.user = user
        self.destroy()


class MainWindow(tk.Tk):
    def __init__(self, db, user):
        super().__init__()
        self.db, self.user, self.logged_out = db, user, False
        self.title(APP_TITLE)
        self.minsize(900, 560)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        header = ttk.Frame(self, padding=(16, 12))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(header, text=f"{user['full_name']} ({ROLE_NAMES[user['role']]})").pack(side="right")

        nav = ttk.Frame(self, padding=(16, 0))
        nav.pack(fill="x")
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.frames = {}
        pages = [("add", "Добавить книгу", AddBookFrame), ("books", "Список книг", BooksFrame),
                 ("search", "Поиск", SearchFrame), ("issue", "Выдать книгу", IssueFrame),
                 ("return", "Вернуть книгу", ReturnFrame), ("loans", "Выданные книги", LoansFrame)]
        for key, text, cls in pages:       # каждый раздел создаётся один раз — окна не плодятся
            frame = cls(container, self)
            frame.grid(row=0, column=0, sticky="nsew")
            self.frames[key] = frame
            btn = ttk.Button(nav, text=text, command=lambda k=key: self.show(k))
            btn.pack(side="left", padx=(0, 6))
            if key == "add" and user["role"] != "admin":
                btn.config(state="disabled")      # добавлять книги может только администратор
        ttk.Button(nav, text="Выйти из аккаунта", command=self.logout).pack(side="right")

        center(self, 960, 620)
        self.show("books")

    def show(self, key):
        self.frames[key].refresh()
        self.frames[key].tkraise()

    def refresh_all(self):
        """Обновляет все разделы после любого изменения данных."""
        for frame in self.frames.values():
            frame.refresh()

    def logout(self):
        self.logged_out = True
        self.destroy()


def main():
    db = Database()
    while True:
        login = LoginWindow(db)
        login.mainloop()
        if login.user is None:          # нажали «Выход» или закрыли окно
            break
        window = MainWindow(db, login.user)
        window.mainloop()
        if not window.logged_out:       # закрыли главное окно крестиком
            break


if __name__ == "__main__":
    main()
