"""
Десктоп-приложение для печати ценников Step Up.
Список товаров + превью + пачечная печать на Xprinter XP-365B.
"""

import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

import price_tag as pt

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

PREVIEW_W, PREVIEW_H = pt.WIDTH, pt.HEIGHT  # 480 x 320, реальный размер этикетки в точках

# В собранном PyInstaller-exe (--onefile) __file__ указывает на временную
# папку распаковки, а не на папку с .exe — поэтому берём путь к самому exe.
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(_BASE_DIR, "tags_list.json")


class PriceTagApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Ценники — Step Up")
        self.geometry("1280x680")
        self.minsize(1120, 600)

        self.items: list[dict] = self._load_items()
        self.editing_index: int | None = None
        self._print_cancel = threading.Event()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_form()
        self._build_list()
        self._build_preview()

        self._refresh_tree()
        self._update_preview()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- форма слева ----------
    def _build_form(self):
        form = ctk.CTkFrame(self, width=280, corner_radius=12)
        form.grid(row=0, column=0, padx=(16, 8), pady=16, sticky="ns")
        form.grid_propagate(False)

        ctk.CTkLabel(form, text="Новый ценник",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(18, 14), padx=18, anchor="w")

        self.e_model = self._field(form, "Модель")
        self.e_price = self._field(form, "Цена")
        self.e_old_price = self._field(form, "Старая цена (если акция)")
        self.e_size = self._field(form, "Размер")
        self.e_shelf = self._field(form, "Полка")
        self.e_copies = self._field(form, "Копий", default="1")

        for e in (self.e_model, self.e_price, self.e_old_price,
                  self.e_size, self.e_shelf, self.e_copies):
            e.bind("<KeyRelease>", lambda _ev: self._update_preview())

        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.pack(pady=(18, 4), padx=18, fill="x")

        self.btn_add = ctk.CTkButton(btns, text="Добавить в список", command=self._add_item)
        self.btn_add.pack(fill="x", pady=4)

        self.btn_save_edit = ctk.CTkButton(btns, text="Сохранить изменения",
                                            command=self._save_edit, state="disabled",
                                            fg_color="#2f8b57", hover_color="#256e45")
        self.btn_save_edit.pack(fill="x", pady=4)

        ctk.CTkButton(btns, text="Очистить форму", fg_color="transparent",
                      border_width=1, command=self._clear_form).pack(fill="x", pady=4)

        dev_frame = ctk.CTkFrame(form, fg_color="transparent")
        dev_frame.pack(pady=(24, 4), padx=18, fill="x", side="bottom")

        if sys.platform == "win32":
            ctk.CTkLabel(dev_frame, text="Принтер").pack(anchor="w")
            printers = pt.list_printers()
            default = pt.DEVICE if pt.DEVICE in printers else (printers[0] if printers else "")
            self.printer_var = tk.StringVar(value=default)
            self.printer_menu = ctk.CTkOptionMenu(dev_frame, variable=self.printer_var,
                                                   values=printers or ["Принтер не найден"])
            self.printer_menu.pack(fill="x", pady=(4, 0))
            ctk.CTkButton(dev_frame, text="Обновить список принтеров", fg_color="transparent",
                          border_width=1, command=self._refresh_printers).pack(fill="x", pady=(6, 0))
        else:
            ctk.CTkLabel(dev_frame, text="Устройство принтера").pack(anchor="w")
            self.e_device = ctk.CTkEntry(dev_frame)
            self.e_device.insert(0, pt.DEVICE)
            self.e_device.pack(fill="x", pady=(4, 0))
            self._bind_editing_shortcuts(self.e_device)

    def _refresh_printers(self):
        printers = pt.list_printers()
        self.printer_menu.configure(values=printers or ["Принтер не найден"])
        if printers and self.printer_var.get() not in printers:
            self.printer_var.set(printers[0])

    def _get_device(self) -> str:
        if sys.platform == "win32":
            return self.printer_var.get()
        return self.e_device.get().strip() or pt.DEVICE

    def _field(self, parent, label, default=""):
        ctk.CTkLabel(parent, text=label, text_color="#a0a0a0",
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=18, pady=(8, 0))
        e = ctk.CTkEntry(parent)
        if default:
            e.insert(0, default)
        e.pack(fill="x", padx=18)
        self._bind_editing_shortcuts(e)
        return e

    @staticmethod
    def _select_all_text(event):
        event.widget.select_range(0, "end")
        event.widget.icursor("end")
        return "break"

    @staticmethod
    def _paste_replacing_selection(event):
        widget = event.widget
        try:
            clipboard = widget.clipboard_get()
        except tk.TclError:
            return "break"
        if widget.selection_present():
            widget.delete("sel.first", "sel.last")
        widget.insert("insert", clipboard)
        return "break"

    def _bind_editing_shortcuts(self, entry):
        entry.bind("<Control-a>", self._select_all_text)
        entry.bind("<Control-A>", self._select_all_text)
        entry.bind("<Control-v>", self._paste_replacing_selection)
        entry.bind("<Control-V>", self._paste_replacing_selection)

    # ---------- список по центру ----------
    def _build_list(self):
        mid = ctk.CTkFrame(self, corner_radius=12)
        mid.grid(row=0, column=1, padx=8, pady=16, sticky="nsew")
        mid.grid_rowconfigure(2, weight=1)
        mid.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(mid, text="Список ценников",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 8))

        self.e_search = ctk.CTkEntry(mid, placeholder_text="Поиск: модель, размер, полка, цена...")
        self.e_search.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
        self.e_search.bind("<KeyRelease>", lambda _ev: self._refresh_tree())
        self._bind_editing_shortcuts(self.e_search)

        columns = ("model", "price", "old_price", "size", "shelf", "copies")
        headers = {"model": "Модель", "price": "Цена", "old_price": "Старая цена",
                   "size": "Размер", "shelf": "Полка", "copies": "Копии"}

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#242424", fieldbackground="#242424",
                        foreground="#e8e8e8", rowheight=30, borderwidth=0, font=("", 12))
        style.configure("Treeview.Heading", background="#1a1a1a", foreground="#e8e8e8",
                        font=("", 12, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#1f6aa5")])

        self.tree = ttk.Treeview(mid, columns=columns, show="headings", selectmode="extended")
        widths = {"model": 260, "price": 90, "old_price": 100, "size": 80, "shelf": 70, "copies": 70}
        for c in columns:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor="center")
        self.tree.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 8))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        list_btns = ctk.CTkFrame(mid, fg_color="transparent")
        list_btns.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))

        ctk.CTkButton(list_btns, text="Удалить выбранные", fg_color="#8b2f2f",
                      hover_color="#6e2424", command=self._remove_selected).pack(side="left")
        self.btn_print_sel = ctk.CTkButton(list_btns, text="Печать выбранных",
                                            command=lambda: self._print(selected_only=True))
        self.btn_print_sel.pack(side="left", padx=8)
        self.btn_stop_print = ctk.CTkButton(list_btns, text="Остановить печать", fg_color="#8b2f2f",
                                             hover_color="#6e2424", state="disabled",
                                             command=self._cancel_print)
        self.btn_stop_print.pack(side="left", padx=8)
        self.btn_print_all = ctk.CTkButton(list_btns, text="Печать всех",
                                            command=lambda: self._print(selected_only=False))
        self.btn_print_all.pack(side="right")

        bulk_btns = ctk.CTkFrame(mid, fg_color="transparent")
        bulk_btns.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 18))
        ctk.CTkButton(bulk_btns, text="Изменить цены для выбранных...",
                      command=self._open_bulk_price_dialog).pack(side="left")

        self.status_label = ctk.CTkLabel(mid, text="", text_color="#8fbf8f", anchor="w")
        self.status_label.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 12))

    # ---------- превью справа ----------
    def _build_preview(self):
        right = ctk.CTkFrame(self, corner_radius=12)
        right.grid(row=0, column=2, padx=(8, 16), pady=16, sticky="ns")

        ctk.CTkLabel(right, text="Превью", font=ctk.CTkFont(size=18, weight="bold")).pack(
            pady=(18, 12), padx=18, anchor="w")

        preview_box = ctk.CTkFrame(right, fg_color="#ffffff", corner_radius=8)
        preview_box.pack(padx=18, pady=(0, 18))
        self.preview_label = ctk.CTkLabel(preview_box, text="")
        self.preview_label.pack(padx=6, pady=6)

    # ---------- данные формы ----------
    def _get_form_data(self) -> dict:
        model = self.e_model.get().strip()
        price_s = self.e_price.get().strip()
        old_price_s = self.e_old_price.get().strip()
        size = self.e_size.get().strip()
        shelf = self.e_shelf.get().strip()
        copies_s = self.e_copies.get().strip() or "1"

        if not model:
            raise ValueError("Укажите модель товара")
        if not price_s:
            raise ValueError("Укажите цену")
        try:
            price = int(price_s)
        except ValueError:
            raise ValueError("Цена должна быть целым числом")

        old_price = None
        if old_price_s:
            try:
                old_price = int(old_price_s)
            except ValueError:
                raise ValueError("Старая цена должна быть целым числом")

        try:
            copies = max(1, int(copies_s))
        except ValueError:
            raise ValueError("Количество копий должно быть целым числом")

        return {
            "model": model,
            "price": price,
            "old_price": old_price,
            "size": size or None,
            "shelf": shelf or None,
            "copies": copies,
        }

    def _fill_form(self, data: dict):
        self.e_model.delete(0, "end"); self.e_model.insert(0, data["model"])
        self.e_price.delete(0, "end"); self.e_price.insert(0, str(data["price"]))
        self.e_old_price.delete(0, "end")
        if data.get("old_price"):
            self.e_old_price.insert(0, str(data["old_price"]))
        self.e_size.delete(0, "end")
        if data.get("size"):
            self.e_size.insert(0, data["size"])
        self.e_shelf.delete(0, "end")
        if data.get("shelf"):
            self.e_shelf.insert(0, str(data["shelf"]))
        self.e_copies.delete(0, "end"); self.e_copies.insert(0, str(data.get("copies", 1)))

    def _clear_form(self):
        for e in (self.e_model, self.e_price, self.e_old_price, self.e_size, self.e_shelf):
            e.delete(0, "end")
        self.e_copies.delete(0, "end")
        self.e_copies.insert(0, "1")
        self.editing_index = None
        self.btn_save_edit.configure(state="disabled")
        self.tree.selection_remove(self.tree.selection())
        self._update_preview()

    # ---------- превью ----------
    def _show_preview_message(self, text: str):
        # CTkLabel не очищает внутренний tkinter-Label при image=None (остаётся
        # висячая ссылка на удалённый PhotoImage), поэтому рисуем сообщение
        # как картинку того же размера вместо переключения на текстовый режим.
        img = Image.new("RGB", (PREVIEW_W, PREVIEW_H), "white")
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(pt.F_REG, 20)
        lines = pt._wrap(d, text, font, PREVIEW_W - 60)
        y = (PREVIEW_H - len(lines) * 30) // 2
        for ln in lines:
            pt._center(d, ln, font, y, img_w=PREVIEW_W, fill=(140, 140, 140))
            y += 30
        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(PREVIEW_W, PREVIEW_H))
        self.preview_label.configure(image=ctk_img, text="")
        self.preview_label.image = ctk_img

    def _update_preview(self):
        try:
            data = self._get_form_data()
        except ValueError as e:
            self._show_preview_message(str(e))
            return
        try:
            img = pt.render_tag(data["model"], data["price"], data["old_price"],
                                data["size"], data["shelf"])
        except Exception as e:
            self._show_preview_message(f"Не удалось построить превью:\n{e}")
            return
        rgb = img.convert("RGB")
        ctk_img = ctk.CTkImage(light_image=rgb, dark_image=rgb, size=(PREVIEW_W, PREVIEW_H))
        self.preview_label.configure(image=ctk_img, text="")
        self.preview_label.image = ctk_img

    # ---------- сохранение списка между запусками ----------
    def _load_items(self) -> list[dict]:
        if not os.path.exists(DATA_FILE):
            return []
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save_items(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)

    def _on_close(self):
        self._save_items()
        self.destroy()

    # ---------- список ----------
    @staticmethod
    def _matches_search(item: dict, words: list[str]) -> bool:
        haystack = " ".join(str(v) for v in (
            item["model"], item.get("size") or "", item.get("shelf") or "",
            item["price"], item.get("old_price") or "",
        )).lower()
        return all(w in haystack for w in words)

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        query = self.e_search.get().strip().lower() if hasattr(self, "e_search") else ""
        words = query.split()
        for i, it in enumerate(self.items):
            if words and not self._matches_search(it, words):
                continue
            self.tree.insert("", "end", iid=str(i), values=(
                it["model"], it["price"], it["old_price"] or "-",
                it["size"] or "-", it["shelf"] or "-", it["copies"],
            ))

    def _add_item(self):
        try:
            data = self._get_form_data()
        except ValueError as e:
            messagebox.showerror("Ошибка", str(e))
            return
        self.items.append(data)
        self._refresh_tree()
        self._save_items()
        self._set_status(f"Добавлено: {data['model']}")
        self._clear_form()

    def _save_edit(self):
        if self.editing_index is None:
            return
        try:
            data = self._get_form_data()
        except ValueError as e:
            messagebox.showerror("Ошибка", str(e))
            return
        self.items[self.editing_index] = data
        self._refresh_tree()
        self._save_items()
        self._set_status(f"Изменено: {data['model']}")
        self._clear_form()

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        for iid in sorted(sel, key=int, reverse=True):
            del self.items[int(iid)]
        self._refresh_tree()
        self._save_items()
        self._clear_form()
        self._set_status("Выбранные ценники удалены")

    # ---------- массовое редактирование цены ----------
    def _apply_bulk_price(self, indices: list[int], field: str, mode: str, value: int) -> tuple[int, int]:
        """Применяет установку/изменение цены к items[indices]. Возвращает (изменено, пропущено)."""
        changed = 0
        skipped = 0
        for idx in indices:
            item = self.items[idx]
            if mode == "set":
                item[field] = value
                changed += 1
            else:
                current = item.get(field)
                if current is None:
                    skipped += 1
                    continue
                item[field] = max(0, current + value)
                changed += 1
        return changed, skipped

    def _open_bulk_price_dialog(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Нет выбора",
                                   "Выберите позиции в списке для массового изменения цены")
            return
        indices = [int(i) for i in sel]

        dialog = ctk.CTkToplevel(self)
        dialog.title("Изменить цены")
        dialog.geometry("380x340")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"Выбрано позиций: {len(indices)}",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(pady=(18, 12), padx=20, anchor="w")

        ctk.CTkLabel(dialog, text="Поле", text_color="#a0a0a0").pack(anchor="w", padx=20)
        field_var = tk.StringVar(value="price")
        field_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        field_frame.pack(fill="x", padx=20, pady=(2, 12))
        ctk.CTkRadioButton(field_frame, text="Цена", variable=field_var, value="price").pack(side="left")
        ctk.CTkRadioButton(field_frame, text="Старая цена", variable=field_var,
                           value="old_price").pack(side="left", padx=(16, 0))

        ctk.CTkLabel(dialog, text="Действие", text_color="#a0a0a0").pack(anchor="w", padx=20)
        mode_var = tk.StringVar(value="set")
        mode_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20, pady=(2, 12))
        ctk.CTkRadioButton(mode_frame, text="Установить значение",
                          variable=mode_var, value="set").pack(anchor="w")
        ctk.CTkRadioButton(mode_frame, text="Изменить на (можно со знаком минус)",
                          variable=mode_var, value="delta").pack(anchor="w", pady=(6, 0))

        ctk.CTkLabel(dialog, text="Значение", text_color="#a0a0a0").pack(anchor="w", padx=20)
        value_entry = ctk.CTkEntry(dialog, placeholder_text="например 500 или -500")
        value_entry.pack(fill="x", padx=20, pady=(2, 0))
        self._bind_editing_shortcuts(value_entry)
        value_entry.focus_set()

        def apply_bulk_edit():
            raw = value_entry.get().strip()
            try:
                value = int(raw)
            except ValueError:
                messagebox.showerror("Ошибка", "Введите целое число (можно со знаком минус)")
                return

            field = field_var.get()
            mode = mode_var.get()
            if mode == "set" and value < 0:
                messagebox.showerror("Ошибка", "Значение цены не может быть отрицательным")
                return

            changed, skipped = self._apply_bulk_price(indices, field, mode, value)

            self._refresh_tree()
            self._save_items()
            self._clear_form()
            msg = f"Цены обновлены: {changed}"
            if skipped:
                msg += f", пропущено без старой цены: {skipped}"
            self._set_status(msg)
            dialog.destroy()

        btns = ctk.CTkFrame(dialog, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=20, side="bottom")
        ctk.CTkButton(btns, text="Применить", command=apply_bulk_edit).pack(side="right")
        ctk.CTkButton(btns, text="Отмена", fg_color="transparent", border_width=1,
                      command=dialog.destroy).pack(side="right", padx=(0, 8))
        value_entry.bind("<Return>", lambda _ev: apply_bulk_edit())

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if len(sel) != 1:
            return
        idx = int(sel[0])
        self.editing_index = idx
        self._fill_form(self.items[idx])
        self.btn_save_edit.configure(state="normal")
        self._update_preview()

    # ---------- печать ----------
    def _print(self, selected_only: bool):
        if selected_only:
            sel = self.tree.selection()
            if not sel:
                messagebox.showwarning("Нет выбора", "Выберите хотя бы один ценник в списке")
                return
            to_print = [self.items[int(i)] for i in sorted(sel, key=int)]
        else:
            if not self.items:
                messagebox.showwarning("Пустой список", "Список ценников пуст")
                return
            to_print = list(self.items)

        device = self._get_device()
        total = len(to_print)

        self._print_cancel = threading.Event()
        self.btn_print_all.configure(state="disabled")
        self.btn_print_sel.configure(state="disabled")
        self.btn_stop_print.configure(state="normal")
        self._set_status(f"Печать: 0 из {total}...", color="#e0c060")

        def worker():
            printed = 0
            try:
                for it in to_print:
                    if self._print_cancel.is_set():
                        break
                    pt.print_tag(
                        model=it["model"], price=it["price"], old_price=it.get("old_price"),
                        size=it.get("size"), shelf=it.get("shelf"),
                        copies=it.get("copies", 1), device=device,
                    )
                    printed += 1
                    self.after(0, lambda p=printed: self._set_status(
                        f"Печать: {p} из {total}...", color="#e0c060"))
            except Exception as e:
                self.after(0, lambda: self._print_failed(e, printed))
            else:
                self.after(0, lambda: self._print_done(printed, total))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_print(self):
        self._print_cancel.set()
        self.btn_stop_print.configure(state="disabled")
        self._set_status("Останавливаю печать...", color="#e0c060")

    def _reset_print_buttons(self):
        self.btn_print_all.configure(state="normal")
        self.btn_print_sel.configure(state="normal")
        self.btn_stop_print.configure(state="disabled")

    def _print_done(self, printed: int, total: int):
        self._reset_print_buttons()
        if printed < total:
            self._set_status(f"Печать остановлена: {printed} из {total}", color="#e0c060")
        else:
            self._set_status(f"Напечатано: {printed} шт.", color="#8fbf8f")

    def _print_failed(self, error: Exception, printed: int):
        self._reset_print_buttons()
        self._set_status(f"Ошибка печати (напечатано {printed})", color="#e07a7a")
        messagebox.showerror("Ошибка печати", str(error))

    def _set_status(self, text: str, color: str = "#8fbf8f"):
        self.status_label.configure(text=text, text_color=color)


if __name__ == "__main__":
    app = PriceTagApp()
    app.mainloop()
