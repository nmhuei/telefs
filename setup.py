from setuptools import setup, find_packages

setup(
    name="telefs",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "telefs = telefs.cli:main",
        ],
    },
    install_requires=[
        "telethon>=1.34.0",
        "tqdm>=4.66.0",
        "cryptography>=41.0.0",
    ],
    author="Antigravity",
    description="Telegram as a remote filesystem",
    license="MIT",
    python_requires=">=3.7",
)
