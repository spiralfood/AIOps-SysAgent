import matplotlib
import customtkinter as ctk


###解决中文乱码问题#######
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
matplotlib.rcParams['axes.unicode_minus'] = False

###设置界面颜色#########
ctk.set_appearance_mode('light')
ctk.set_default_color_theme('blue')


####大模型API########
DEEPSEEK_API_URL = 'Your_apy_key'
DEEPSEEK_API_KEY = 'sk-903b5c491acb4b1295f2b090eef90ad2'
