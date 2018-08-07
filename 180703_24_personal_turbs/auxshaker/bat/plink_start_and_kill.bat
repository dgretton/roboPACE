@echo off
rem %1: absolute path to a bat file that starts plink with a putty session and a file name as arguments
rem %2: name of serial putty session
rem %3: absolute path to a text file to send over serial
start /min /wait taskkill /f /im putty.exe
start /min /wait taskkill /f /im plink.exe
cmd.exe /C "start /min cmd.exe /C %1 %2 %3"
PING 1.1.1.1 -n 2 -w 600 >NUL
start /min /wait taskkill /f /im plink.exe
exit