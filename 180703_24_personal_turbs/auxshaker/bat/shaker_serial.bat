@echo off
rem %1: name of serial putty session
rem %2: file to send over plink
plink %1 < %2