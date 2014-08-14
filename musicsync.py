"""
    musicsync.py

    Provides a utility class around the Google Music API that allows for easy syncing of playlists.
    Currently it will look at all the files already in the playlist and:
     Upload any missing files (and add them to the playlist)
     Add any files that are already uploaded but not in the online playlist
     Optionally remove any files from the playlist that are not in the local copy (does not delete
     files!)
     Uploads are done one by one followed by a playlist update for each file (rather than as a
     batch)
    It does not remove duplicate entries from playlists or handle multiple entries.

    TODO: Add optional duplicate remover

    API used: https://github.com/simon-weber/Unofficial-Google-Music-API
    Thanks to: Kevion Kwok and Simon Weber

    Use at your own risk - especially for existing playlists

    Free to use, reuse, copy, clone, etc

    Usage:
     ms = MusicSync()
     # Will prompt for Email and Password - if 2-factor auth is on you'll need to generate a one-
       time password
     ms.sync_playlist("c:/path/to/playlist.m3u")

     ms.delete_song("song_id")
"""
__author__ = "Tom Graham"
__email__ = "tom@sirwhite.com"


from gmusicapi import Webclient, Mobileclient, Musicmanager
from gmusicapi.clients import OAUTH_FILEPATH
import mutagen
import json
import os
import time
import re
import codecs
from getpass import getpass
from httplib import BadStatusLine, CannotSendRequest
import sys

MAX_UPLOAD_ATTEMPTS_PER_FILE = 3
MAX_CONNECTION_ERRORS_BEFORE_QUIT = 5
STANDARD_SLEEP = 5
MAX_SONGS_IN_PLAYLIST = 1000
LOCAL_OAUTH_FILE = './oauth.cred'

#sys.stdout = codecs.getwriter('cp1252')(sys.stdout)

class MusicSync(object):
    def __init__(self, email=None, password=None):
        self.mm = Musicmanager()
        self.wc = Webclient()
        self.mc = Mobileclient()
        if not email:
            email = raw_input("Email: ")
        if not password:
            password = getpass()

        self.email = email
        self.password = password

        self.logged_in = self.auth()

        print "Fetching playlists from Google..."
        self.playlists = self.mc.get_all_user_playlist_contents()
        #self.playlists = self.mc.get_all_playlists()
        #self.playlists = self.wc.get_all_playlist_ids(auto=False)
        self.all_songs = self.mc.get_all_songs()
        #print "Got %d playlists." % len(self.playlists['user'])
        print "Got %d playlists containing %d songs." % (len(self.playlists), len(self.all_songs))
        print ""


    def auth(self):
        self.logged_in = self.mc.login(self.email, self.password)
        #self.logged_in = self.wc.login(self.email, self.password)
        if not self.logged_in:
            print "Login failed..."
            exit()

        print ""
        print "Logged in as %s" % self.email
        print ""

        if not os.path.isfile(OAUTH_FILEPATH):
            print "First time login. Please follow the instructions below:"
            self.mm.perform_oauth()
        self.logged_in = self.mm.login()
        if not self.logged_in:
            print "OAuth failed... try deleting your %s file and trying again." % OAUTH_FILEPATH
            exit()

        print "Authenticated"
        print ""


    def sync_playlist(self, filename, remove_missing):
    #def sync_playlist(self, filename, remove_missing=False):
        filename = self.get_platform_path(filename)
        os.chdir(os.path.dirname(filename))
        title = os.path.splitext(os.path.basename(filename))[0]
        print "Syncing playlist: %s" % filename
        #if title not in self.playlists['user']:
            #print "   didn't exist... creating..."
            #self.playlists['user'][title] = [self.wc.create_playlist(title)]
        print ""

        plid = ""

        for pl in self.playlists:
            if pl['name'] == title:
                plid = pl['id']
                goog_songs = pl['tracks']

        if plid == "":
            print "   didn't exist... creating..."
            plid = self.mc.create_playlist(self, title)

        #plid = self.playlists['user'][title][0]
        #goog_songs = self.wc.get_playlist_songs(plid)
        print "%d songs already in Google Music playlist" % len(goog_songs)
        pc_songs = self.get_files_from_playlist(filename)
        print "%d songs in local playlist" % len(pc_songs)
        print ""

        # Sanity check max 1000 songs per playlist
        if len(pc_songs) > MAX_SONGS_IN_PLAYLIST:
            print "    Google music doesn't allow more than %d songs in a playlist..." % MAX_SONGS_IN_PLAYLIST
            print "    Will only attempt to sync the first %d songs." % MAX_SONGS_IN_PLAYLIST
            del pc_songs[MAX_SONGS_IN_PLAYLIST:]

        existing_files = 0
        added_files = 0
        failed_files = 0
        removed_files = 0
        fatal_count = 0

        for fn in pc_songs:
            if self.file_already_in_list(fn, goog_songs, self.all_songs):
                existing_files += 1
                continue
            print ""
            print "Adding: %s" % os.path.basename(fn).encode('cp1252')
            #print "Adding: %s" % os.path.basename(fn)
            #online = False
            online = self.find_song(fn, goog_songs, self.all_songs)
            #online = self.find_song(fn)
            song_id = None
            if online:
                song_id = online['id']
                print "   already uploaded [%s]" % song_id
            else:
                attempts = 0
                result = []
                while not result and attempts < MAX_UPLOAD_ATTEMPTS_PER_FILE:
                    print "   uploading... (may take a while)"
                    attempts += 1
                    try:
                        result = self.mm.upload(fn)
                    except (BadStatusLine, CannotSendRequest):
                        # Bail out if we're getting too many disconnects
                        if fatal_count >= MAX_CONNECTION_ERRORS_BEFORE_QUIT:
                            print ""
                            print "Too many disconnections - quitting. Please try running the script again."
                            print ""
                            exit()

                        print "Connection Error -- Reattempting login"
                        fatal_count += 1
                        self.wc.logout()
                        self.mc.logout()
                        self.mm.logout()
                        result = []
                        time.sleep(STANDARD_SLEEP)

                    except:
                        result = []
                        time.sleep(STANDARD_SLEEP)

                try:
                    if result[0]:
                        song_id = result[0].itervalues().next()
                    else:
                        song_id = result[1].itervalues().next()
                    print "   upload complete [%s]" % song_id
                except:
                    print "      upload failed - skipping"
                    tag = self.get_id3_tag(fn)
                    print "      failed song:\t%s\t%s\t%s" % (tag['title'].encode('cp1252'), tag['artist'].encode('cp1252'), tag['album'].encode('cp1252'))

            if not song_id:
                failed_files += 1
                continue

            added = self.mc.add_songs_to_playlist(plid, song_id)
            time.sleep(.3) # Don't spam the server too fast...
            print "   done adding to playlist"
            added_files += 1

        if remove_missing:
            for g in goog_songs:
                for s in self.all_songs:
                    if g['trackId'] == s['id']:
                        print ""
                        print "Removing: %s" % s['title'].encode('cp1252')
                        self.mc.remove_entries_from_playlist(g['id'])
                        #self.wc.remove_songs_from_playlist(plid, s.id)
                        time.sleep(.3) # Don't spam the server too fast...
                        removed_files += 1

        print ""
        print "---"
        print "%d songs unmodified" % existing_files
        print "%d songs added" % added_files
        print "%d songs failed" % failed_files
        print "%d songs removed" % removed_files


    def get_files_from_playlist(self, filename):
        files = []
        f = codecs.open(filename, encoding='cp1252')
        #f = codecs.open(filename, encoding='utf-8')
        for line in f:
            line = line.rstrip().replace(u'\ufeff',u'')
            if line == "" or line[0] == "#":
                continue
            path  = os.path.abspath(self.get_platform_path(line))
            if not os.path.exists(path):
                print "File not found: %s" % line
                continue
            files.append(path)
        f.close()
        return files

    def file_already_in_list(self, filename, goog_songs, all_songs):
        tag = self.get_id3_tag(filename)
        print "Searching for\t%s\t%s\t%s" % (tag['title'].encode('cp1252'), tag['artist'].encode('cp1252'), tag['album'].encode('cp1252'))
        i = 0
        while i < len(goog_songs):
            for s in all_songs:
                if goog_songs[i]['trackId'] == s['id']:
                    if self.tag_compare(s, tag):
                        print "Found match\t%s\t%s\t%s" % (s['title'].encode('cp1252'), s['artist'].encode('cp1252'), s['album'].encode('cp1252'))
                        goog_songs.pop(i)
                        return True
            i += 1
        return False

    def get_id3_tag(self, filename):
        data = mutagen.File(filename, easy=True)
        r = {}
        if 'title' not in data:
            title = os.path.splitext(os.path.basename(filename))[0]
            print 'Found song with no ID3 title, setting using filename:'
            print '  %s' % title
            print '  (please note - the id3 format used (v2.4) is invisible to windows)'
            data['title'] = [title]
            data.save()
        r['title'] = data['title'][0]
        r['track'] = int(data['tracknumber'][0].split('/')[0]) if 'tracknumber' in data else 0
        # If there is no track, try and get a track number off the front of the file... since thats
        # what google seems to do...
        # Not sure how google expects it to be formatted, for now this is a best guess
        if r['track'] == 0:
            m = re.match("(\d+) ", os.path.basename(filename))
            if m:
                r['track'] = int(m.group(0))
        r['artist'] = data['artist'][0] if 'artist' in data else ''
        r['album'] = data['album'][0] if 'album' in data else ''
        return r

    def find_song(self, filename, goog_songs, all_songs):
        tag = self.get_id3_tag(filename)
        print "Searching for\t%s\t%s\t%s" % (tag['title'].encode('cp1252'), tag['artist'].encode('cp1252'), tag['album'].encode('cp1252'))
        #results = self.wc.search(tag['title'])
        # NOTE - diagnostic print here to check results if you're creating duplicates
        #print results['song_hits']
        #for r in goog_songs:
        #for r in results['song_hits']:
        for s in all_songs:
            #if r['trackId'] == s['id']:
            if self.tag_compare(s, tag):
                # TODO: add rough time check to make sure its "close"
                print "Found match\t%s\t%s\t%s" % (s['title'].encode('cp1252'), s['artist'].encode('cp1252'), s['album'].encode('cp1252'))
                return s
        return None

    def tag_compare(self, g_song, tag):
        # If a google result has no track, google doesn't return a field for it
        if 'title' not in g_song:
            g_song['title'] = ""
        if 'artist' not in g_song:
            g_song['artist'] = ""
        if 'album' not in g_song:
            g_song['album'] = ""
        if 'track' not in g_song:
            g_song['track'] = 0
        if (g_song['title'].lower() == tag['title'].lower() and g_song['artist'].lower() == tag['artist'].lower()) or\
           (g_song['album'].lower() == tag['album'].lower() and g_song['title'].lower() == tag['title'].lower()) or\
           (g_song['artist'].lower() == tag['artist'].lower() and g_song['album'].lower() == tag['album'].lower() and g_song['track'] == tag['track']):
            print "Partial match\t%s\t%s\t%s" % (g_song['title'].encode('cp1252'), g_song['artist'].encode('cp1252'), g_song['album'].encode('cp1252'))
        return g_song['title'].lower() == tag['title'].lower() and\
               g_song['artist'].lower() == tag['artist'].lower() and\
               g_song['album'].lower() == tag['album'].lower() #and\
               #g_song['track'] == tag['track']

    def delete_song(self, sid):
        self.mc.delete_songs(sid)
        print "Deleted song by id [%s]" % sid

    def get_platform_path(self, full_path):
        # Try to avoid messing with the path if possible
        if os.sep == '/' and '\\' not in full_path:
            return full_path
        if os.sep == '\\' and '\\' in full_path:
            return full_path
        if '\\' not in full_path:
            return full_path
        return os.path.normpath(full_path.replace('\\', '/'))
