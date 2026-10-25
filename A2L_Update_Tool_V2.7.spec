# -*- mode: python ; coding: utf-8 -*-


block_cipher = None


a = Analysis(
    ['A2L_Update_Tool_main.py'],
    pathex=[],
    binaries=[],
    # 启动脚本 (.bat/.cmd) 与 exe 同级放置即可，不需要打进 _internal。
    # data/ 下的示例数据随包分发，便于开箱复现论文中的生产规模统计。
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
    # rag_align 只依赖 Python 标准库；numpy / sentence-transformers 是可选的
    # 检索增强，模块内为延迟导入并带 try/except 降级。PyInstaller 会静态分析
    # 到它们，进而把 torch / scipy 整个科学计算栈（数 GB）拖进分发包，这里显式排除。
    # 打包版因此走零依赖的 CharNgram 词法检索；需要语义嵌入时用源码方式运行并
    # 自行安装 sentence-transformers（见 README）。
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
        # faiss / cv2 只在稠密检索后端下可达，而该后端依赖已被排除的 numpy
        # 与 sentence-transformers，在打包版里根本到不了；两者还会顺带拖入
        # OpenBLAS 与 ffmpeg（约 150 MB 的无效负载），故一并排除。
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
    [],
    exclude_binaries=True,
    name='A2L_Update_Tool_V2.7',
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='A2L_Update_Tool_V2.7',
)
