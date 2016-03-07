#!/usr/bin/env python2

"""To do: copy front covers """

__version__ = "2.1"

import os, sys, struct, re, glob, optparse, time
import Crypto.Cipher.AES, Crypto.Util.Counter
import mutagen
import sqlite3
from os import rename
from shutil import copy

import superadb


class PlayMusicDecrypter:
    """Decrypt MP3 file from Google Play Music offline storage (All Access)"""
    def __init__(self, database, infile):
        # Open source file
        self.infile = infile
        self.source = open(infile, "rb")

        # Get file info
        self.database = database
        self.info = self.get_info()

    def encrypted(self):
        # Test if source file is encrypted, if not, it is (hopefully) a normal mp3 file
        start_bytes = self.source.read(4)
        if start_bytes != "\x12\xd3\x15\x27":
            return False
        else:
            return True

    def decrypt(self):
        """Decrypt one block"""
        data = self.source.read(1024)
        if not data:
            return ""

        iv = data[:16]
        encrypted = data[16:]

        counter = Crypto.Util.Counter.new(64, prefix=iv[:8], initial_value=struct.unpack(">Q", iv[8:])[0])
        cipher = Crypto.Cipher.AES.new(self.info["CpData"], Crypto.Cipher.AES.MODE_CTR, counter=counter)

        return cipher.decrypt(encrypted)

    def decrypt_all(self, outfile=""):
        """Decrypt all blocks and write them to outfile (or to stdout if outfile is not specified)"""
        destination = open(outfile, "wb") if outfile else sys.stdout
        while True:
            decrypted = self.decrypt()
            if not decrypted:
                break

            destination.write(decrypted)
            destination.flush()

    def get_info(self):
        """Returns informations about song from database"""
        db = sqlite3.connect(self.database, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()

        cursor.execute("""SELECT Title, Album, Artist, AlbumArtist, Composer, Genre, Year, Duration,
                                 TrackCount, TrackNumber, DiscCount, DiscNumber, Compilation, CpData
                          FROM music
                          WHERE LocalCopyPath = ?""", (os.path.basename(self.infile),))
        row = cursor.fetchone()
        if row:
            return dict(row)
        else:
            raise ValueError("Empty file info!")

    def normalize_filename(self, filename):
        """Remove invalid characters from filename"""
        return unicode(re.sub(r'[<>:"/\\|?*]', " ", filename))

    def get_newdir(self): #without filename
        structure = os.path.join(self.normalize_filename(self.info["AlbumArtist"]), self.normalize_filename(self.info["Album"]))
        return structure

    def get_newname(self):
        return self.normalize_filename(u"{TrackNumber:02d} - {Title}.mp3".format(**self.info))

    def update_id3(self, outfile):
        """Update ID3 tags in outfile"""
        audio = mutagen.File(outfile, easy=True)
        audio.add_tags()
        audio["title"] = self.info["Title"]
        audio["album"] = self.info["Album"]
        audio["artist"] = self.info["Artist"]
        audio["performer"] = self.info["AlbumArtist"]
        audio["composer"] = self.info["Composer"]
        audio["genre"] = self.info["Genre"]
        audio["date"] = str(self.info["Year"])
        audio["tracknumber"] = str(self.info["TrackNumber"])
        audio["discnumber"] = str(self.info["DiscNumber"])
        audio["compilation"] = str(self.info["Compilation"])
        audio.save()


def is_empty_file(filename):
    """Returns True if file doesn't exist or is empty"""
    return False if os.path.isfile(filename) and os.path.getsize(filename) > 0 else True


def pull_database(destination_dir=".", adb="adb"):
    """Pull Google Play Music database from device"""
    print("Downloading Google Play Music database from device...")
    try:
        adb = superadb.SuperAdb(executable=adb)
    except RuntimeError:
        print("  Device is not connected! Exiting...")
        sys.exit(1)

    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)

    db_file = os.path.join(destination_dir, "music.db")
    adb.pull("/data/data/com.google.android.music/databases/music.db", db_file)
    if is_empty_file(db_file):
        print("  Download failed! Exiting...")
        sys.exit(1)


def pull_library(source_dir="/data/data/com.google.android.music/files/music", destination_dir="encrypted", adb="adb"):
    """Pull Google Play Music library from device"""
    print("Downloading encrypted MP3 files from device...")
    try:
        adb = superadb.SuperAdb(executable=adb)
    except RuntimeError:
        print("  Device is not connected! Exiting...")
        sys.exit(1)

    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)

    files = [f for f in adb.ls(source_dir) if f.endswith(".mp3")]
    if files:
        start_time = time.time()
        for i, f in enumerate(files):
            sys.stdout.write("\r  Downloading file {}/{}...".format(i + 1, len(files)))
            sys.stdout.flush()
            adb.pull(os.path.join(source_dir, f), os.path.join(destination_dir, f))
        print("")
        print("  All downloads finished ({:.1f}s)!".format(time.time() - start_time))
    else:
        print("  No files found! Exiting...")
        sys.exit(1)


def decrypt_files(source_dir="encrypted", destination_dir=".", database="music.db"):
    """Decrypt all MP3 files in source directory and write them to destination directory"""

    print("Decrypting MP3 files...")
    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)

    files = glob.glob(os.path.join(source_dir, "*.mp3"))

    if files:
        start_time = time.time()
        for in_file in files:
            decrypter = PlayMusicDecrypter(database, in_file)

            out_file = os.path.join(destination_dir, decrypter.get_newdir(), decrypter.get_newname())
            out_dir = os.path.join(destination_dir, decrypter.get_newdir())

            print(u"  Decrypting file {} -> {}".format(in_file, out_file))

            if not os.path.isdir(os.path.dirname(out_file)):
                os.makedirs(os.path.dirname(out_file))

            if decrypter.encrypted() is True:
                decrypter.decrypt_all(out_file)
            else:
                copy(in_file, out_dir)
                rename(os.path.join(out_dir, os.path.basename(in_file)), out_file)
                
            decrypter.update_id3(out_file)
            os.remove(in_file)
        print("  Decryption finished ({:.1f}s)!".format(time.time() - start_time))
    else:
        print("  No files found! Exiting...")
        sys.exit(1)


def main():
    # Parse command line options
    parser = optparse.OptionParser(description="Decrypt MP3 files from Google Play Music offline storage (All Access)",
                                   usage="usage: %prog [-h] [options] [destination_dir]",
                                   version="%prog {}".format(__version__))
    parser.add_option("-a", "--adb", default="adb",
                      help="path to adb executable")
    parser.add_option("-d", "--database",
                      help="local path to Google Play Music database file (will be downloaded from device via adb if not specified)")
    parser.add_option("-l", "--library",
                      help="local path to directory with encrypted MP3 files (will be downloaded from device via adb if not specified")
    parser.add_option("-r", "--remote", default="/data/data/com.google.android.music/files/music",
                      help="remote path to directory with encrypted MP3 files on device (default: %default)")
    (options, args) = parser.parse_args()

    if len(args) < 1:
        destination_dir = "."
    else:
        destination_dir = args[0]

    # Download Google Play Music database from device via adb
    if not options.database:
        options.database = os.path.join(destination_dir, "music.db")
        pull_database(destination_dir, adb=options.adb)

    # Download encrypted MP3 files from device via adb
    if not options.library:
        options.library = os.path.join(destination_dir, "encrypted")
        pull_library(options.remote, options.library, adb=options.adb)

    # Decrypt all MP3 files
    decrypt_files(options.library, destination_dir, options.database)

if __name__ == "__main__":
    main()
