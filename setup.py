from setuptools import setup, find_packages

setup(
    name='sample-dump',
    package_dir={'': 'src'},
    packages=find_packages('src'),
    install_requires=[
        'pydent',
        'boto3'
    ]
)