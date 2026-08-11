from setuptools import find_packages, setup


setup(
    name="paper-repro-agent",
    version="0.1.0",
    packages=find_packages(include=["backend", "backend.*"]),
    include_package_data=True,
    install_requires=[
        "pydantic>=2.10,<3",
        "pypdf>=5,<7",
        "docling>=2.70,<3",
        "typing-extensions>=4.12",
    ],
    author="PaperReproAgent contributors",
    description="AI paper experiment reproduction platform",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    python_requires=">=3.11",
)
