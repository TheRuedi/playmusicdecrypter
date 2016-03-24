#!/usr/bin/env python2

__version__ = "3.0"

import os, sys, struct, re, glob, optparse, time
import Crypto.Cipher.AES, Crypto.Util.Counter
import mutagen
from mutagen.mp3 import MP3
import mutagen.id3
import sqlite3
from os import rename
from shutil import copy
import subprocess
import shutil


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
                                 TrackCount, TrackNumber, DiscCount, DiscNumber, Compilation, AlbumArtLocation, CpData
                          FROM MUSIC
                          WHERE LocalCopyPath = ?""", (os.path.basename(self.infile),))
        row = cursor.fetchone()
        if row:
            return dict(row)
        else:
            raise ValueError("Empty file info!")

    def get_cover(self, AlbumArtLocation=''):
        db = sqlite3.connect(self.database, detect_types=sqlite3.PARSE_DECLTYPES)
        cursor = db.execute("""SELECT RemoteLocation, LocalLocation FROM ARTWORK_CACHE""")    #RemoteLocation is the URL of the cover, LocalLocation the filename.
        for row in cursor:
            if row[0] == AlbumArtLocation:
                return row[1]                              #returns filename
		return "Error"

    def normalize_filename(self, filename):
        """Remove invalid characters from filename"""
        return unicode(re.sub(r'[<>:"/\\|?*]', " ", filename))

    def get_newdir(self): #without filename
        structure = os.path.join(self.normalize_filename(self.info["AlbumArtist"]), self.normalize_filename(self.info["Album"]))  # "/Britney Spears/Femme Fatale/"
        return structure

    def get_newname(self):
        return self.normalize_filename(u"{TrackNumber:02d} - {Title}.mp3".format(**self.info))                                    # "1234.mp3" --> "01 - I Wanna Go.mp3"

    def update_id3(self, outfile):
        """Update ID3 tags in outfile"""
        try:
            audio = mutagen.id3.ID3(outfile, v2_version=3)
        except mutagen.id3.error:
            audio = mutagen.id3.ID3()
            audio.save(outfile, v2_version=3)

        audio.add(mutagen.id3.TIT2(encoding=3, text=self.info["Title"]))
        audio.add(mutagen.id3.TALB(encoding=3, text=self.info["Album"]))
        audio.add(mutagen.id3.TPE1(encoding=3, text=self.info["Artist"]))         #Artist
        audio.add(mutagen.id3.TPE2(encoding=3, text=self.info["Artist"]))         #Album Artist
        audio.add(mutagen.id3.TCOM(encoding=3, text=self.info["Composer"]))
        audio.add(mutagen.id3.TCON(encoding=3, text=self.info["Genre"]))          #Content type (Genre)
        audio.add(mutagen.id3.TYER(encoding=3, text=str(self.info["Year"])))             #Year of recording
        audio.add(mutagen.id3.TRCK(encoding=3, text=str(self.info["TrackNumber"])))

        try:
            if self.get_cover(self.info["AlbumArtLocation"]) is "Error":
                print("Error: No artwork found although link exists!!! -> No cover")
            else:
                artwork = os.path.join( os.getcwd(), "tmp", "artwork", self.get_cover(self.info["AlbumArtLocation"]) )
                if artwork[-3:] == "jpg":
                    command = "convert " + artwork + " -quality 95 " + artwork[:-3] + "png"   #I use Imagemagick to convert apng into jpg
                    subprocess.call(command, shell=True)
                    artwork = artwork[:-3] + "jpg"
                    audio.add(mutagen.id3.APIC(encoding=3, mime=u'image/jpeg', type=3, desc=u'', data=open(artwork).read()))
                elif artwork[-3:] == "png":
                    command = "convert " + artwork + " -quality 95 " + artwork[:-3] + "jpg" #I convert jpgs as well, sincs my Sony Walkman only displays a special kind of jpg
                    subprocess.call(command, shell=True)
                    artwork = artwork[:-3] + "jpg"
                    audio.add(mutagen.id3.APIC(encoding=3, mime=u'image/jpeg', type=3, desc=u'', data=open(artwork).read()))
                else:
                    print("Error: Artwork is neither jpg nor png!!! -> No cover")
        except AttributeError:                                                      #if self.info["AlbumArtLocation"] is empty
            print("Error: No link for artwork found!!! -> No cover")

        audio.save(outfile, v2_version=3)

def is_empty_file(filename):
    """Returns True if file doesn't exist or is empty"""
    return False if os.path.isfile(filename) and os.path.getsize(filename) > 0 else True

def decrypt_files(source_dir, destination_dir=".", database="music.db"):
    """Decrypt all MP3 files in source directory and write them to destination directory"""
    print(source_dir)
    print("Decrypting MP3 files...")
    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)

    files = glob.glob(os.path.join(source_dir, "*.mp3"))                # /path of pmd/tmp/music/*.mp3

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
        shutil.rmtree(os.path.join(os.getcwd(), "tmp"))
    else:
        print("  No files found! Exiting...")
        sys.exit(1)


def main():
    # Parse command line options
    parser = optparse.OptionParser(description="Decrypt MP3 files from Google Play Music offline storage (All Access)",
                                   usage="usage: %prog [-h] [options] [destination_dir]",
                                   version="%prog {}".format(__version__))
    parser.add_option("-a", "--artwork",
                      help="local path to directory with encrypted artwork files (will be downloaded from device via adb if not specified")
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
        options.database = os.path.join(os.getcwd(), "tmp", "music.db")                                                                                 #/path of pmd/tmp/music.db 
        command = "mkdir tmp; cd tmp && adb root && adb pull /data/data/com.google.android.music/databases/music.db"
        subprocess.call(command, shell=True) 

    # Download encrypted MP3 files from device via adb
    if not options.library:
        options.library = os.path.join(os.getcwd(), "tmp", "music")                                                                                     #/path of pmd/tmp/music/
        command = "mkdir tmp; cd tmp && mkdir music && cd music && adb root && adb pull /data/data/com.google.android.music/files/music && rm '.nomedia'"
        subprocess.call(command, shell=True)

    # Download encrypted Artwork files from device via adb
    if not options.artwork:
       options.artwork = os.path.join(os.getcwd(), "tmp", "artwork")
       command = "mkdir tmp; cd tmp && mkdir artwork && cd artwork && adb root && adb pull /data/data/com.google.android.music/files/artwork"
       subprocess.call(command, shell=True)

    # Decrypt all MP3 files
    decrypt_files(options.library, destination_dir, options.database)

if __name__ == "__main__":
    main()
