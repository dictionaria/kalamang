from setuptools import setup


setup(
    name='cldfbench_kalamang',
    py_modules=['cldfbench_kalamang'],
    include_package_data=True,
    zip_safe=False,
    entry_points={
        'cldfbench.dataset': [
            'kalamang=cldfbench_kalamang:Dataset',
        ]
    },
    install_requires=[
        'cldfbench',
        'pyglottolog',
        'pydictionaria>=2.0',
    ],
    extras_require={
        'test': [
            'pytest-cldf',
        ],
    },
)
