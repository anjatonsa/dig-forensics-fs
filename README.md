# File system forensics

Primeri poziva alata
### Hide files
```bash
sudo python3 slack_tool.py hide -d /dev/sdb1 -f file1.txt file2.pdf -m /mnt/testusb -s ./sat.json 
 ```

### Extract hidden files
 ```bash
sudo python3 slack_tool.py read -d /dev/sdb1 -f file1.txt file2.pdf -m /mnt/testusb -s ./sat.json -o ./output
 ```

### Delete hidden files
 ```bash
sudo python3 slack_tool.py delete -d /dev/sdb1 -f file1.txt  -m /mnt/testusb -s ./sat.json 
 ```


### List hidden files
 ```bash
sudo python3 slack_tool.py list -d /dev/sdb1 -s ./sat.json -m /mnt/testusb
 ```

