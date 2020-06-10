from setuptools import setup, find_packages

setup(
    name='aquarium-provenance',
    package_dir={'': 'src'},
    packages=find_packages('src'),
    install_requires=[
        'pydent==0.0.35',
        'boto3',
        'pySBOL'
    ]
)
