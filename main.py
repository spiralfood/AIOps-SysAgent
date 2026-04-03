import customtkinter as ctk
import config
from main_window import SystemMonitorApp

if __name__ == '__main__':
    root = ctk.CTk()
    app = SystemMonitorApp(root)
    root.mainloop()
