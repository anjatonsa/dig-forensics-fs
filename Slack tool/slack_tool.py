import subprocess
import os
from datetime import datetime
import tarfile
import gzip
import json
from io import BytesIO
import hashlib
import argparse

class DriveHandler:
    
    def __init__(self, mount_path, sector_size=512, block_size=4096):
        self.mount_path = mount_path
        self.sector_size = sector_size
        self.block_size = block_size
        
        self.device = self._get_device_path()
        print(f"[Drive handler info]: Device identified - {self.device}")
        
        self.file_paths = []                     
        self.slack_space_info = []              
        self.available_slack_locations = []       # Sorted list of all slack sectors
    
    def _get_device_path(self):
        try:
            output = subprocess.check_output(['df', '-h', self.mount_path])
            output_str = output.decode('utf-8')
            lines = output_str.strip().split('\n')
            device = lines[-1].split()[0]
            return device

        except Exception as e:
            print(f"[Drive handler error]:Error getting device path: {e}")
            return None
    
    def discover_all_files(self):

        print("[Drive handler info]: Discovering all files on the filesystem...")
        file_count = 0
        
        for directory, subdirs, filenames in os.walk(self.mount_path):
            for filename in filenames:
                full_path = os.path.join(directory, filename)
                self.file_paths.append(full_path)
                file_count += 1
        
        print(f"[Drive handler info]: Found {file_count} files")
        return self.file_paths
    
    def analyze_file_for_slack_space(self, file_path):

        #print("[Drive handler info]: Analyzing the file -", file_path)
        try:
            # get file metadata
            stat_info = os.stat(file_path)
            file_size = stat_info.st_size
            inode_number = stat_info.st_ino

            if file_size == 0:
                return None
            
            cmd = ['debugfs', '-R', f'stat <{inode_number}>', self.device]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"[-] debugfs error for {file_path}: {result.stderr}")
                return None
            
            extent_info = self._parse_extents(result.stdout)

            if not extent_info:
                return None
            
            # calculating slack space
            extent_start_block = extent_info['start_block']
            extent_end_block = extent_info['end_block']
            
            # converting blocks to byte offsets
            extent_start_bytes = extent_start_block * self.block_size
            extent_end_bytes = (extent_end_block + 1) * self.block_size  

            # actual ending of a file
            file_end_bytes = extent_start_bytes + file_size
            
            slack_start = file_end_bytes
            slack_end = extent_end_bytes
            slack_size = slack_end - slack_start
            
            return {
                'path': file_path,
                'inode': inode_number,
                'file_size': file_size,
                'slack_start': slack_start,
                'slack_end': slack_end,
                'slack_size': slack_size
            }
            
        except Exception as e:
            print(f"[-] Error analyzing {file_path}: {e}")
            return None
    
    def _parse_extents(self, debugfs_output):
        all_extents = []
        
        if 'EXTENTS:' not in debugfs_output:
            return None
        
        extent_section = debugfs_output.split('EXTENTS:')[1]

        for line in extent_section.split('\n'):
            line = line.strip()
            if not line or ':' not in line:
                continue
            
            try:
                # Format: (logical_range):physical_range
                physical_part = line.split(':')[1]
                
                if '-' in physical_part:
                    start, end = physical_part.split('-')
                    all_extents.append({
                        'start_block': int(start),
                        'end_block': int(end)
                    })
                else:
                    # Single block format
                    block = int(physical_part)
                    all_extents.append({
                        'start_block': block,
                        'end_block': block
                    })
            except (ValueError, IndexError):
                continue
        
        if not all_extents:
            return None
        
        return all_extents[-1]
    
    def map_all_slack_space(self):

        print("[Drive handler info]: Mapping slack space across all files...")
        
        slack_count = 0
        total_slack_bytes = 0
        
        for file_path in self.file_paths:
            slack_info = self.analyze_file_for_slack_space(file_path)
            
            if slack_info and slack_info['slack_size'] > 0:
                self.slack_space_info.append(slack_info)
                slack_count += 1
                total_slack_bytes += slack_info['slack_size']
        
        print(f"[Drive handler info]: Found {slack_count} slack cluster spaces")
        print(f"[Drive handler info]: Total slack space available: {total_slack_bytes / 1024 / 1024:.2f} MB")
        
        self._convert_ranges_to_sectors()
        
        return self.slack_space_info
    
    def _convert_ranges_to_sectors(self):
        
        #converting slack space byte ranges to sector offsets
        print("[Drive handler info]: Converting byte ranges to sector offsets...")
        
        for slack_info in self.slack_space_info:
            slack_start = slack_info['slack_start']
            slack_end = slack_info['slack_end']
            
            for sector_offset in range(slack_start, slack_end, self.sector_size):
                if sector_offset < slack_end:
                    self.available_slack_locations.append(sector_offset)
        
        self.available_slack_locations.sort()
        print(f"[Drive handler info]: Mapped {len(self.available_slack_locations)} available sectors")


class FileHandler:
    
    def __init__(self, sector_size=512):
        self.sector_size = sector_size
    
    def create_tar_archive(self, files_to_hide):

        print(f"[File handler info]: Creating tar.gz archive from {len(files_to_hide)} file(s)...")
        try:
            tar_buffer = BytesIO()
            
            with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
                for file_path in files_to_hide:
                    if not os.path.isfile(file_path):
                        print(f"[-] Warning: {file_path} not found, skipping")
                        continue
                    arcname = os.path.basename(file_path)
                    tar.add(file_path, arcname=arcname)
            
            tar_data = tar_buffer.getvalue()
            print(f"[File handler info] Uncompressed tar size: {len(tar_data) / 1024:.2f} KB")
            
            compressed_buffer = BytesIO()
            with gzip.GzipFile(fileobj=compressed_buffer, mode='wb') as gz:
                gz.write(tar_data)
            
            compressed_blob = compressed_buffer.getvalue()
            blob_size = len(compressed_blob)
            
            print(f"[File handler info]: Compressed tar.gz size: {blob_size / 1024:.2f} KB")
            print(f"[File handler info]: Compression ratio: {(1 - blob_size / len(tar_data)) * 100:.1f}%")
            
            return compressed_blob, blob_size
            
        except Exception as e:
            print(f"[File handler error]: Error creating tar archive: {e}")
            return None
    
    def split_into_chunks(self, compressed_blob):
        
        print(f"[File handler info]: Splitting blob into {self.sector_size}-byte chunks...")
        chunks = []
        
        # Split blob into slack sector-sized pieces
        for i in range(0, len(compressed_blob), self.sector_size):
            chunk = compressed_blob[i:i + self.sector_size]
            
            # If the last chunk is smaller than sector_size, pad it with zeros
            if len(chunk) < self.sector_size:
                chunk = chunk + b'\x00' * (self.sector_size - len(chunk))
            
            chunks.append(chunk)
                
        print(f"[File handler info]: Created {len(chunks)} chunks")
        
        return chunks, len(chunks)
    
    def extract_files(self, blob, output_directory,):

        print(f"[File handler info]: Extracting files to {output_directory}...")
        
        os.makedirs(output_directory, exist_ok=True)
        
        try:
            compressed_buffer = BytesIO(blob)
            
            with gzip.GzipFile(fileobj=compressed_buffer, mode='rb') as gz:
                with tarfile.open(fileobj=gz, mode='r|') as tar:
                    tar.extractall(path=output_directory)
                    members = tar.getmembers()
            
            extracted_files = [m.name for m in members if m.isfile()]
            
            print(f"[File handler info] Successfully extracted {len(extracted_files)} files:")
            for fname in extracted_files:
                print(f"    - {fname}")
            
            return extracted_files
            
        except Exception as e:
            print(f"[File handler error]: Error extracting files: {e}")
            return None


class StorageManager:
   
    def __init__(self, available_slack_sectors, sat_file_path, device_path, sector_size=512):
        self.available_slack_sectors = available_slack_sectors.copy()  
        self.used_sectors = set()                                      
        self.sat_file_path = sat_file_path
        self.sector_size = sector_size
        self.device_path = device_path
        self.sat = self._load_sat_from_disk()                                            

    def write_to_slack(self, blob_id, blob_chunks, sectors_needed, original_size):
        
        free_sectors = [s for s in self.available_slack_sectors if s not in self.used_sectors]
        print("Free sectors",free_sectors[0:7])
        if len(free_sectors) < sectors_needed:
            print(f"[Storage manager info]: Not enough slack space. Need {sectors_needed}, have {len(free_sectors)}")
            return None
        
        allocated_sectors = free_sectors[:sectors_needed]
        print("Aloccated sectors: ", allocated_sectors)
        first_sector = allocated_sectors[0]
        last_sector = allocated_sectors[-1]
        
        # write chunks to disk
        try:
            self._write_to_device(blob_chunks, allocated_sectors)
        except Exception as e:
            print(f"[Storage manager error]: Error writing to device: {e}")

        # mark sectors as used
        for sector in allocated_sectors:
            self.used_sectors.add(sector)
        
        # add record to SAT
        sat_entry = {
            'file_id': blob_id,
            'first_sector': first_sector,
            'last_sector': last_sector,
            'num_sectors': sectors_needed,
            'data_size': original_size,
            'timestamp': datetime.now().isoformat()
        }
        self.sat.append(sat_entry)
        
        print(f"[Storage manager info]: Wrote '{blob_id}' to sectors {first_sector}-{last_sector} ({sectors_needed} sectors)")
        return sat_entry
    
    def read_from_slack(self, blob_id):
        
        # find the SAT entry
        sat_entry = None
        for entry in self.sat:
            if entry['file_id'] == blob_id:
                sat_entry = entry
                break
        
        if not sat_entry:
            print(f"[Storage manager info]: File '{blob_id}' not found in SAT")
            return None
        
        #read file from sectors
        first_sector = sat_entry['first_sector']
        last_sector = sat_entry['last_sector']
        data_size = sat_entry['data_size']
        
        sectors_to_read = [
            sector for sector in self.available_slack_sectors
            if first_sector <= sector <= last_sector
        ]
        
        file_data = self._read_from_device(sectors_to_read, data_size)
        
        print(f"[Storage manager info]: Read '{blob_id}' from sectors {first_sector}-{sat_entry['last_sector']}")
        return file_data
    
    def _write_to_device(self, chunks, sectors):
        try:
            with open(self.device_path, 'r+b') as device:
                for chunk, sector_offset in zip(chunks, sectors):
                    device.seek(sector_offset)
                    device.write(chunk)

            print(f"[Storage manager info]: Successfully wrote {len(chunks)} sectors")
        except Exception as e:
            print(f"[Storage manager error]: Error writing to device: {e}")
    
    def _read_from_device(self, sectors, expected_size):
        try:
            data = b''
            with open(self.device_path, 'rb') as device:
                for sector_offset in sectors:
                    device.seek(sector_offset)
                    sector_data = device.read(self.sector_size)
                    data += sector_data
            
            # remove padding
            data = data[:expected_size]
            print(f"[Storage manager info]: Successfully read {len(data)} bytes from device")
            return data
        except Exception as e:
            print(f"[Storage manager error]: Error reading from device: {e}")
            return None
    
    def delete_from_slack(self, blob_id):

        sat_entry = None
        for entry in self.sat:
            if entry['file_id'] == blob_id:
                sat_entry = entry
                break

        if not sat_entry:
            print(f"[Storage manager info]: File '{blob_id}' not found in SAT")
            return False

        # determine sectors occupied by the file
        first_sector = sat_entry['first_sector']
        last_sector = sat_entry['last_sector']

        sectors_to_clear = [
            sector for sector in self.available_slack_sectors
            if first_sector <= sector <= last_sector
        ]
        
        print("sectors to clear", sectors_to_clear)

        # Overwrite sectors with zeros
        self._delete_from_device(sectors_to_clear)

        # remove SAT entry
        self.sat.remove(sat_entry)


        print(
            f"[Storage manager info]: Deleted '{blob_id}' "
            f"from sectors {first_sector}-{last_sector}"
        )

        return True
    
    def _delete_from_device(self, sectors_to_clear):
        try:
            with open(self.device_path, 'r+b') as device:
                zero_block = bytes(self.sector_size)

                for sector_offset in sectors_to_clear:
                    device.seek(sector_offset)
                    device.write(zero_block)
                device.flush()
        
        except Exception as e:
            print(f"[Storage manager error]: Error clearing sectors: {e}")
            return False

    def save_sat_to_disk(self):
        try:
            with open(self.sat_file_path, 'w') as f:
                json.dump(self.sat, f, indent=2)
            print(f"[Storage manager info]: SAT saved to {self.sat_file_path}")
        except Exception as e:
            print(f"[Storage manager error]: Error saving SAT: {e}")
    
    def _load_sat_from_disk(self):
        sat_table=[]
        try:
            with open(self.sat_file_path, 'r') as f:
                sat_table = json.load(f)
            
            self.used_sectors = set()
            for entry in sat_table:
                first = entry['first_sector']
                num = entry['num_sectors']
                for i in range(num):
                    self.used_sectors.add(first + i*self.sector_size)
            
            print(f"[Storage manager info]: SAT loaded from {self.sat_file_path} ({len(sat_table)} files)")
        
        except Exception as e:
            with open(self.sat_file_path, 'w') as f:
                json.dump(sat_table, f)
            print(f"[Storage manager error]: No available SAT, making a new one.")
        return sat_table
    
    def list_hidden(self):
        if not self.sat:
            print("[Storage manager info]: No data hidden yet")
            return
        
        print("[Storage manager info]: Hidden files in slack space:")
        print("-" * 80)
        for entry in self.sat:
            print(f"  ID: {entry['file_id']}")
            print(f"    Location: sectors {entry['first_sector']}-{entry['last_sector']}")
            print(f"    Size: {entry['data_size']} bytes ({entry['num_sectors']} sectors)")
            print(f"    Added: {entry['timestamp']}")
        print("-" * 80)


def read(device_path, mount_path, sat_file_path, hidden_files, output_path):

    try:
        dh = DriveHandler(mount_path)
        dh.discover_all_files()
        slack_sectors = dh.map_all_slack_space()
    except Exception as e:
        print(f"[-] Error initilazing drive handler: {e}")

    try:
        fh = FileHandler()
    except Exception as e:
        print(f"[-] Error initilazing file handler: {e}")

    try:
        manager = StorageManager(dh.available_slack_locations, sat_file_path, device_path)
        for hidden_file in hidden_files:
            read_blob = manager.read_from_slack(hidden_file)
            fh.extract_files(read_blob, output_path)
    except Exception as e:
        print(f"[-] Error reading hidden files: {e}")
    finally:
        manager.save_sat_to_disk()


def hide(device_path, mount_path, sat_file_path, hide_input):
    
    dh = DriveHandler(mount_path)
    
    dh.discover_all_files()
    dh.map_all_slack_space()    
    manager = StorageManager(dh.available_slack_locations, sat_file_path, device_path)
    
    fh = FileHandler()
    for x in hide_input:
        blob_file, original_size = fh.create_tar_archive([x])
        blob_chunks, sectors_needed  = (fh.split_into_chunks(blob_file))
    
        try:
            manager.write_to_slack(x, blob_chunks, sectors_needed, original_size)
            pass
        except Exception as e:
            print(f"[Hide function error] Error saving file: {e}")

    manager.save_sat_to_disk()


def delete(device_path, mount_path, sat_file_path, hidden_files):
    try:
        dh = DriveHandler(mount_path)
        dh.discover_all_files()
        slack_sectors = dh.map_all_slack_space()
    except Exception as e:
        print(f"[-] Error initilazing drive handler: {e}")

    try:
        fh = FileHandler()
    except Exception as e:
        print(f"[-] Error initilazing file handler: {e}")

    try:
        manager = StorageManager(dh.available_slack_locations, sat_file_path, device_path)
        for hidden_file in hidden_files:
            manager.delete_from_slack(hidden_file)
    except Exception as e:
        print(f"[-] Error reading hidden files: {e}")
    finally:
        manager.save_sat_to_disk()


def list_hidden(device_path, sat_file_path, mount_path):
    
    try:
        dh = DriveHandler(mount_path)
        dh.discover_all_files()
        slack_sectors = dh.map_all_slack_space()
    except Exception as e:
        print(f"[-] Error initilazing drive handler: {e}")
    
    try:
        manager = StorageManager(dh.available_slack_locations, sat_file_path, device_path)
        manager.list_hidden()
    except Exception as e:
        print(f"[Hide function error]: Error reading list of hidden files: {e}")
    finally:
        manager.save_sat_to_disk()


def main():
    parser = argparse.ArgumentParser(
        description="Slack Space Data Hiding Tool"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True
    )


    hide_parser = subparsers.add_parser(
        "hide",
        help="Hide a file in slack space"
    )

    hide_parser.add_argument(
        "-f", "--files",
        nargs="+",
        required=True,
        help="Files to hide"
    )

    hide_parser.add_argument(
        "-d", "--device_path",
        required=True,
        help="Target device (e.g. /dev/sdb1)"
    )
    
    hide_parser.add_argument(
        "-m", "--mount_path",
        required=True,
        help="Where is device mounted(e.g. /mnt/testusb)"
    )
    
    hide_parser.add_argument(
        "-s", "--sat_file_path",
        required=True,
        help="File path for SAT json"
    )


    read_parser = subparsers.add_parser(
        "read",
        help="Extract hidden file"
    )

    read_parser.add_argument(
        "-f", "--files",
        nargs="+",
        required=True,
        help="Files to read"
    )

    read_parser.add_argument(
        "-d", "--device_path",
        required=True,
        help="Target device (e.g. /dev/sdb1)"
    )
    
    read_parser.add_argument(
        "-m", "--mount_path",
        required=True,
        help="Where is device mounted(e.g. /mnt/testusb)"
    )

    read_parser.add_argument(
        "-s", "--sat_file_path",
        required=True,
        help="File path for SAT json"
    )

    read_parser.add_argument(
        "-o", "--output_path",
        required=True,
        help="Output path for read files"
    )

    delete_parser = subparsers.add_parser(
        "delete",
        help="Delete hidden file"
    )

    delete_parser.add_argument(
        "-f", "--files",
        nargs="+",
        required=True,
        help="Files to delete"
    )

    delete_parser.add_argument(
        "-d", "--device_path",
        required=True,
        help="Target device (e.g. /dev/sdb1)"
    )
    
    delete_parser.add_argument(
        "-m", "--mount_path",
        required=True,
        help="Where is device mounted(e.g. /mnt/testusb)"
    )

    delete_parser.add_argument(
        "-s", "--sat_file_path",
        required=True,
        help="File path for SAT json"
    )

    list_parser = subparsers.add_parser(
        "list",
        help="List hidden files"
    )
    
    list_parser.add_argument(
        "-s", "--sat_file_path",
        required=True,
        help="File path for SAT json"
    )

    list_parser.add_argument(
        "-d", "--device_path",
        required=True,
        help="Target device (e.g. /dev/sdb1)"
    )
    
    list_parser.add_argument(
        "-m", "--mount_path",
        required=True,
        help="Where is device mounted(e.g. /mnt/testusb)"
    )

    args = parser.parse_args()


    if args.command == "hide":
        device_path = args.device_path
        mount_path = args.mount_path
        sat_file_path = args.sat_file_path
        input_files = args.files

        print(f"Hiding {input_files} on {device_path}")
        hide(device_path, mount_path, sat_file_path, input_files)

    elif args.command == "read":
        device_path = args.device_path
        mount_path = args.mount_path
        sat_file_path = args.sat_file_path
        hidden_files = args.files
        output_path = args.output_path

        print(f"Reading files {hidden_files} from {device_path}")
        read(device_path, mount_path, sat_file_path, hidden_files, output_path)
    
    elif args.command == "delete":
        device_path = args.device_path
        mount_path = args.mount_path
        sat_file_path = args.sat_file_path
        hidden_files = args.files

        print(f"Deleting files {hidden_files} from {device_path}")
        delete(device_path, mount_path, sat_file_path, hidden_files)

    elif args.command == "list":
        device_path = args.device_path
        sat_file_path = args.sat_file_path
        mount_path = args.mount_path

        print(f"Listing hidden files on {device_path}")
        list_hidden(device_path, sat_file_path, mount_path)


if __name__ == '__main__':

    main()



