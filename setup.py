from setuptools import setup, find_packages

setup(
    name="nmhuei-telefs",
    version="0.2.12",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "telefs = telefs.cli:main",
        ],
    },
    install_requires=[
        "telethon>=1.34.0",
        "cryptography>=41.0.0",
        "keyring>=24.0.0",
        "tqdm>=4.66.0",
        "rich>=13.0.0",
        "nest-asyncio>=1.5.0",
    ],
    author="Antigravity",
    description="Telegram as a remote filesystem",
    license="MIT",
    python_requires=">=3.7",
)
