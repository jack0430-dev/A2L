# -*- mode: python ; coding: utf-8 -*-
# 单文件版打包配置（onefile）：生成一个自包含的 exe，可单独复制分发。
#
# 与 A2L_Update_Tool_V2.7.spec（onedir）的差异：
#   - 无 COLLECT：所有二进制与数据打进 exe 本体（PyInstaller 运行时解压到
#     sys._MEIPASS 临时目录；rag_align 里基于 __file__ 的相对定位会解析到
#     解压目录，data/ 就在那里，因此 datas 无需改动）。
#   - EXE 需要 a.binaries 与 a.datas。
# 代价：启动时要先解压（首启慢数秒），且单文件 exe 更容易被杀软误报；
# 需要更友好的安装体验时改用 Inno Setup 包 onedir 版（见 README）。

block_cipher = None


a = Analysis(
    ['A2L_Update_Tool_main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('data/sample', 'data/sample'),
        ('data/TcuBsw.axf', 'data'),
    ],
    hiddenimports=[
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'i18n',
        'rag_align',
        'rag_bridge',
        'openpyxl',
        'elftools',
        'elftools.elf.elffile',
        'elftools.common',
        'elftools.dwarf',
        'elftools.elf',
        'elftools.elf.sections',
        'elftools.elf.descriptions',
        'elftools.elf.dynamic',
        'elftools.elf.gnuversions',
        'elftools.elf.relocation',
        'elftools.elf.segments',
        'elftools.construct',
        'elftools.construct.lib',
        'elftools.construct.adapters',
        'elftools.construct.core',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 可选的科学计算栈全部排除（理由同 onedir spec）：打包版走零依赖的
    # CharNgram 词法检索，语义嵌入后端只在源码方式下可用。
    excludes=[
        'numpy',
        'scipy',
        'pandas',
        'sklearn',
        'scikit_learn',
        'torch',
        'torchvision',
        'torchaudio',
        'sentence_transformers',
        'transformers',
        'tensorflow',
        'jax',
        'jaxlib',
        'matplotlib',
        'IPython',
        'jupyter',
        'notebook',
        'nbformat',
        'nbconvert',
        'pytest',
        'pip',
        'faiss',
        'cv2',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='A2L_Update_Tool_V2.7_onefile',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
