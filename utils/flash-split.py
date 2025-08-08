#!/usr/bin/env python

"""
Flash Parser for HLK-LD6002 Microwave Radar

This script parses a flash dump file from a HLK-LD6002 microwave radar,
identifies sections according to the flash structure, and displays
the descriptor contents for each section.

The script can also save sections with valid signatures to separate files.

Usage:
    python flash-split.py <flash_file> [options]

Options:
    --save-sections       Save sections with valid signatures to separate files
    --prefix PREFIX       Specify a custom prefix for output files (default: input filename)
"""

import sys
import struct
import os
import argparse
from typing import Dict, Tuple, List

# Flash section definitions based on docs/flash-map-hlk-ld6002.md
FLASH_SECTIONS = [
    {"name": "ota_jump_code", "addr": 0x00000000, "size": 0x0C10, "ram_addr": 0x00008000, 
     "desc": "Loaded by ROM bootloader"},
    {"name": "ota_boot_32k", "addr": 0x00008000, "size": 0x1FB8, "ram_addr": 0x20008000, 
     "desc": "Loaded by jump_code, check Boot Area, load AppX/Factory, update AppX"},
    {"name": "Boot Area", "addr": 0x00010000, "size": 8, "ram_addr": None, 
     "desc": "Boot configuration"},
    {"name": "Radar Config", "addr": 0x00014000, "size": 0x108, "ram_addr": None, 
     "desc": "There are settings like: mounting type, interference and detections areas, etc"},
    {"name": "App1", "addr": 0x00017FF0, "size": None, "ram_addr": 0x00008000, 
     "desc": "Radar App1"},
    {"name": "App2", "addr": 0x00027FF0, "size": None, "ram_addr": 0x00008000, 
     "desc": "Radar App2"},
    {"name": "Factory Default", "addr": 0x00038000, "size": None, "ram_addr": 0x00008000, 
     "desc": "Factory Default"},
    {"name": "Unknown", "addr": 0x00048000, "size": 0x3C, "ram_addr": None, 
     "desc": "Unknown section"}
]

def parse_app_descriptor(data: bytes) -> Dict:
    """
    Parse the 16-byte app descriptor header according to docs/app-descriptor-hlk-ld6002.md
    
    Header format:
    | Index | Size | Description             |
    |:-----:|:----:|-------------------------|
    |   0   |  2   | Size of App             |
    |   2   |  2   | Size of App             |
    |   4   |  1   | 'Z' - Signature of App  |
    |   5   |  4   | Some address. Not used. |
    |   9   |  7   | Not used                |
    
    Returns a dictionary with the parsed header fields.
    """
    if len(data) < 16:
        return {"error": f"Insufficient data for header: {len(data)} bytes"}
    
    # Parse the header fields
    size1 = struct.unpack("<H", data[0:2])[0]  # Little-endian 2-byte unsigned short
    size2 = struct.unpack("<H", data[2:4])[0]  # Little-endian 2-byte unsigned short
    signature = chr(data[4])
    address = struct.unpack("<I", data[5:9])[0]  # Little-endian 4-byte unsigned int
    
    # Create a dictionary with the parsed fields
    descriptor = {
        "size1": size1,
        "size2": size2,
        "signature": signature,
        "address": address,
        "raw_header": data[:16].hex(' '),
        "valid": size1 == size2 and signature == 'Z'
    }
    
    return descriptor

def read_flash_file(filename: str) -> bytes:
    """
    Read the flash file and return its contents as bytes.
    """
    try:
        with open(filename, 'rb') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading flash file: {e}")
        sys.exit(1)

def parse_flash(flash_data: bytes) -> List[Dict]:
    """
    Parse the flash data and extract sections with their descriptors.
    """
    results = []
    
    for section in FLASH_SECTIONS:
        addr = section["addr"]
        size = section["size"]
        name = section["name"]
        
        # Skip if the address is beyond the flash data
        if addr >= len(flash_data):
            results.append({
                "name": name,
                "addr": f"0x{addr:08X}",
                "error": "Address beyond flash data size"
            })
            continue
        
        # Extract section data
        if size is not None:
            section_data = flash_data[addr:addr+size]
        else:
            # For sections without a defined size, we'll just read the header
            section_data = flash_data[addr:addr+16]
        
        # Parse the descriptor if the section is large enough
        if len(section_data) >= 16:
            descriptor = parse_app_descriptor(section_data)
            
            # If the descriptor is valid and size is not defined, use the size from the descriptor
            if size is None and descriptor["valid"]:
                size = descriptor["size1"]
                # Re-extract section data with the correct size
                section_data = flash_data[addr:addr+size]
        else:
            descriptor = {"error": f"Section too small: {len(section_data)} bytes"}
        
        results.append({
            "name": name,
            "addr": f"0x{addr:08X}",
            "size": f"0x{size:X}" if size is not None else "Unknown",
            "ram_addr": f"0x{section['ram_addr']:08X}" if section['ram_addr'] is not None else "N/A",
            "desc": section["desc"],
            "descriptor": descriptor,
            "data_len": len(section_data),
            "data": section_data
        })
    
    return results

def print_results(results: List[Dict]):
    """
    Print the parsing results in a readable format.
    """
    print("\nHLK-LD6002 Flash Parser Results")
    print("=" * 80)
    
    for section in results:
        print(f"\nSection: {section['name']}")
        print(f"  Address: {section['addr']}")
        print(f"  Size: {section['size']}")
        print(f"  RAM Address: {section['ram_addr']}")
        print(f"  Description: {section['desc']}")
        print(f"  Data Length: {section['data_len']} bytes")
        
        if "descriptor" in section:
            desc = section["descriptor"]
            print("  Descriptor:")
            if "error" in desc:
                print(f"    Error: {desc['error']}")
            else:
                print(f"    Size1: 0x{desc['size1']:X} ({desc['size1']} bytes)")
                print(f"    Size2: 0x{desc['size2']:X} ({desc['size2']} bytes)")
                print(f"    Signature: '{desc['signature']}'")
                print(f"    Address: 0x{desc['address']:08X}")
                print(f"    Raw Header: {desc['raw_header']}")
                print(f"    Valid: {desc['valid']}")
        
        print("-" * 80)

def save_sections_to_files(results: List[Dict], prefix: str):
    """
    Save sections with valid signatures to separate files.
    
    Args:
        results: List of section dictionaries from parse_flash
        prefix: Prefix for output filenames
    """
    saved_count = 0
    
    for section in results:
        # Only save sections with valid descriptors
        if "descriptor" in section and "valid" in section["descriptor"] and section["descriptor"]["valid"]:
            # Create a safe filename
            section_name = section["name"].replace(" ", "_").lower()
            filename = f"{prefix}_{section_name}.bin"
            
            try:
                with open(filename, 'wb') as f:
                    f.write(section["data"])
                print(f"Saved section '{section['name']}' to {filename} ({section['data_len']} bytes)")
                saved_count += 1
            except Exception as e:
                print(f"Error saving section '{section['name']}' to {filename}: {e}")
    
    if saved_count == 0:
        print("No sections with valid signatures found to save.")
    else:
        print(f"Saved {saved_count} sections to files with prefix '{prefix}'.")

def main():
    """
    Main function to parse command line arguments and process the flash file.
    """
    parser = argparse.ArgumentParser(description="Flash Parser for HLK-LD6002 Microwave Radar")
    parser.add_argument("flash_file", help="Path to the flash dump file")
    parser.add_argument("--save-sections", action="store_true", help="Save sections with valid signatures to separate files")
    parser.add_argument("--prefix", help="Specify a custom prefix for output files (default: input filename)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.flash_file):
        print(f"Error: File '{args.flash_file}' not found.")
        sys.exit(1)
    
    print(f"Parsing flash file: {args.flash_file}")
    
    # Read the flash file
    flash_data = read_flash_file(args.flash_file)
    print(f"Read {len(flash_data)} bytes from flash file.")
    
    # Parse the flash data
    results = parse_flash(flash_data)
    
    # Print the results
    print_results(results)
    
    # Save sections to files if requested
    if args.save_sections:
        # Use the provided prefix or the input filename (without extension) as default
        prefix = args.prefix if args.prefix else os.path.splitext(os.path.basename(args.flash_file))[0]
        save_sections_to_files(results, prefix)

if __name__ == "__main__":
    main()