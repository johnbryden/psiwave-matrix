#!/usr/bin/env python3

from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np

# Define the Cython extension
extensions = [
    Extension(
        "cython_optimized",
        ["cython_optimized.pyx"],
        include_dirs=[np.get_include()],
        extra_compile_args=["-O3", "-march=native"],  # Optimize for current CPU
        extra_link_args=["-O3"]
    )
]

setup(
    name="matrix-play-cython",
    ext_modules=cythonize(extensions, compiler_directives={
        'language_level': 3,
        'boundscheck': False,  # Disable bounds checking for speed
        'wraparound': False,   # Disable negative indexing for speed
        'cdivision': True,     # Use C division for speed
    }),
    install_requires=[
        'numpy>=1.21.0',
        'cython>=0.29.0',
    ],
    zip_safe=False,
)
