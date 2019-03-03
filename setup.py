#!/usr/bin/env python
# -*- coding: utf-8 -*-

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup


def readme():
    with open('README.md') as f:
        return f.read()


def requirements():
    return list(open('requirements.txt'))


setup(name='zhub',
      version='0.1',
      description='Command line tool for ZenHub',
      long_description=readme(),
      classifiers=[
          'Development Status :: 4 - Beta',
          'License :: OSI Approved :: MIT License',
          'Programming Language :: Python :: 3.6',
          'Programming Language :: Python :: 3.7',
          'Environment :: Console',
          'Intended Audience :: End Users/Desktop',
          'Topic :: Utilities'],
      url='https://github.com/ptpt/zhub',
      author='Tao Peng',
      author_email='ptpttt+zhub@gmail.com',
      keywords='github, zenhub, command-line, client',
      license='MIT',
      py_modules=['zhub'],
      install_requires=requirements(),
      entry_points='''
      [console_scripts]
      zhub=zhub:cli
      ''',
      zip_safe=False)
