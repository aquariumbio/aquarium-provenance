from setuptools import setup, find_packages

setup(
    name='aquarium-provenance',
    version='1.0.1',
    package_dir={'': 'src'},
    packages=find_packages('src'),
    install_requires=[
        'boto3',
        'pySBOL'
    ],

    author="Ben Keller",
    author_email="bjkeller@uw.edu",
    description="Package for capturing PROV-like provenance of files and items in Aquarium",
    url="https://aquariumbio.bio",
    project_urls={
        "Source Code": "https://github.com/aquariumbio/aquarium-provenance"
    }

)
