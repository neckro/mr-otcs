import datetime
import errno
import itertools
import os
import subprocess
import sys
import time
from multiprocessing import Process

#######################################################################
# Configuration.

# Program paths. Use absolute paths.
# ffprobe is optional if HTML schedule will not be used.
MEDIA_PLAYER_PATH = "/usr/bin/ffmpeg"
FFPROBE_PATH = "/usr/bin/ffprobe"

# Arguments to pass to media player. This should be whatever is
# necessary to immediately exit the player after playback is completed.
# MEDIA_PLAYER_BEFORE_ARGUMENTS are passed before the input file.
# MEDIA_PLAYER_AFTER_ARGUMENTS are passed after the input file.
MEDIA_PLAYER_BEFORE_ARGUMENTS = "-hide_banner -re -i"
MEDIA_PLAYER_AFTER_ARGUMENTS = "-filter_complex \"tpad=stop_duration=2;apad=pad_dur=2\" -vcodec libx264 -b:v 1100k -acodec aac -b:a 128k -f flv -framerate 30 -g 60 rtmp://{rtmp_address}"

# Base path for all video files, including trailing slash.
# This path will also contain play_index.txt and play_history.txt.
BASE_PATH = "/media/videos/"

# Video files, including subdirectories. This can be a Python list
# containing strings with filenames in BASE_PATH or a string with a
# path to a text file containing one filename in BASE_PATH per line.
# Items starting with comment characters ; # or // and blank lines will
# be skipped.
MEDIA_PLAYLIST = "/home/pi/list.txt"

# Allow retrying file access if next video file cannot be opened.
# This can be useful if BASE_PATH is a network share.
# If RETRY_ATTEMPTS is set to 0, the script will abort if the next
# video file cannot be found.
# Set RETRY_ATTEMPTS to -1 to retry infinitely.
# RETRY_PERIOD is the delay in seconds between each retry attempt.
RETRY_ATTEMPTS = 0
RETRY_PERIOD = 5

# Number of videos to keep in history log, saved in play_history.txt in
# BASE_PATH. Set to 0 to disable.
PLAY_HISTORY_LENGTH = 10

# Path for HTML schedule.
# See template.html for the file to be read by this script.
# Set to None to disable writing schedule.
SCHEDULE_PATH = "/var/www/schedule.html"

# Number of upcoming shows to write in schedule.
# High settings can cause delays in playing next file.
# Setting too high can cause MemoryError.
SCHEDULE_UPCOMING_LENGTH = 10


#######################################################################
# Function definitions.

def check_file(path):
    """Retry opening nonexistant files up to RETRY_ATTEMPTS."""

    retry_attempts_remaining = RETRY_ATTEMPTS

    # If RETRY_ATTEMPTS is -1, don't print number of attempts
    # remaining.
    if retry_attempts_remaining < 0:
        retry_attempts_string = ""

    while not os.path.isfile(path):
        # Print number of attempts remaining.
        if retry_attempts_remaining > 0:
            if retry_attempts_remaining > 1:
                retry_attempts_string = "{} attempts remaining.\n".format(retry_attempts_remaining)
            else:
                retry_attempts_string = "1 attempt remaining.\n"
            retry_attempts_remaining -= 1

        # If retry_attempts_remaining is 0 and file is not found,
        # raise exception.
        elif retry_attempts_remaining == 0:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT),path)

        print("File not found: {}.\nRetrying in {} seconds...\n{}".format(path,RETRY_PERIOD,retry_attempts_string))

        time.sleep(RETRY_PERIOD)
        continue

    else:
        return


def write_schedule(file_list,previous_file=""):
    """
    Write an HTML file containing file names and lengths read from a list
    containing video file paths. Optionally, include the most recently played
    file as well.
    """

    def get_length(file):
        """Run ffprobe and retrieve length of file."""

        result = subprocess.run([FFPROBE_PATH,"-v","error","-select_streams","v:0",
                                "-show_entries","stream=duration","-of",
                                "default=noprint_wrappers=1:nokey=1",file],
                                capture_output=True,text=True).stdout

        if result == "":
            raise Exception("ffprobe was unable to read duration of: " + file)

        return result

    # next_time contains start times of upcoming videos.
    # For the first file in file_list, this is the current system time.
    # Time is retrieved in UTC, to be converted to user's local time
    # when they load the schedule in their browser.
    next_time = datetime.datetime.utcnow()

    coming_up_next = []

    if previous_file != "":
        previous_file = os.path.splitext(previous_file)[0].replace("\\","/")

    for filename in file_list:
        # Get length of next video in seconds from ffprobe.
        duration = float(get_length(os.path.join(BASE_PATH,filename)))

        # Remove .mp4 extension from file names and convert backslashes
        # to forward slashes.
        filename = os.path.splitext(filename)[0].replace("\\","/")

        # Append duration and stripped filename to list as tuple.
        coming_up_next.append((next_time,filename))

        # Add length of current video to current time and use as
        # starting time for next video.
        next_time = next_time + datetime.timedelta(seconds=duration)

    # Format coming_up_next list into string suitable for assigning as
    # JavaScript array of objects.
    js_array = "[" + ",".join(["{{time:'{}',name:'{}'}}".format(i,n.replace("'",r"\'")) for i,n in coming_up_next]) + "]"

    # Generate HTML contents.
    with open(os.path.join(sys.path[0],"template.html"),"r") as html_template:
        html_contents = html_template.read()

    html_contents = html_contents.format(js_array=js_array,previous_file=previous_file)

    with open(SCHEDULE_PATH,"w") as html_file:
        html_file.write(html_contents)

    # Upload html_file to a publicly accessible location
    # using pysftp or something similar if necessary.

def loop(media_playlist):
    """Loop over playlist indefinitely."""

    # Keep playlist index and store in file play_index.txt. Create it
    # if it does not exist.
    try:
        with open(os.path.join(BASE_PATH,"play_index.txt"),"r") as index_file:
            play_index = int(index_file.read())

    except FileNotFoundError:
        with open(os.path.join(BASE_PATH,"play_index.txt"),"w") as index_file:
            index_file.write("0")
            play_index = 0

    if play_index < len(media_playlist):

        video_time = datetime.datetime.now()
        video_file = media_playlist[play_index]
        video_file_fullpath = os.path.join(BASE_PATH,video_file)

        # Check if video_file exists and raise exception if it does
        # not.
        check_file(video_file_fullpath)

        # Write history of played video files and timestamps,
        # limited to PLAY_HISTORY_LENGTH.
        if PLAY_HISTORY_LENGTH > 0:
            try:
                with open(os.path.join(BASE_PATH,"play_history.txt"),"r") as play_history:
                    play_history_buffer = play_history.readlines()

            except FileNotFoundError:
                with open(os.path.join(BASE_PATH,"play_history.txt"),"w+") as play_history:
                    play_history_buffer = []
                    play_history.close()

            finally:
                with open(os.path.join(BASE_PATH,"play_history.txt"),"w+") as play_history:
                    play_history_buffer.append("{},{}\n".format(video_time,video_file))
                    play_history.writelines(play_history_buffer[-PLAY_HISTORY_LENGTH:])

        print("Now playing: " + video_file)

        # If HTML schedule writing is enabled, retrieve next videos in
        # list up to SCHEDULE_UPCOMING_LENGTH and write_schedule in
        # second process.
        if SCHEDULE_PATH != None:

            # Copy of media list sliced from current video to the end.
            media_progress = media_playlist[play_index:]

            # Pass sliced list to write_schedule.
            if len(media_progress) >= SCHEDULE_UPCOMING_LENGTH:
                media_copy = media_progress[:SCHEDULE_UPCOMING_LENGTH + 1]

            # If media_progress is shorter than
            # SCHEDULE_UPCOMING_LENGTH, copy full media playlist until
            # the correct length is reached.
            else:
                media_copy = (media_progress + list(
                              itertools.islice(itertools.cycle(media_playlist),
                              SCHEDULE_UPCOMING_LENGTH - len(media_progress) + 1)))

            schedule_p = Process(target=write_schedule,args=(media_copy,),
                                 kwargs={"previous_file":media_playlist[play_index - 1]})

            player_p = Process(target=subprocess.run,kwargs={"args":"{} {} \"{}\" {}".format(MEDIA_PLAYER_PATH,MEDIA_PLAYER_BEFORE_ARGUMENTS,video_file_fullpath,MEDIA_PLAYER_AFTER_ARGUMENTS),"shell":True})

            player_p.start()
            schedule_p.start()
            player_p.join()
            schedule_p.join()

        # If scheduling is disabled, simply play files in single
        # process.
        else:
            result = subprocess.run("{} {} \"{}\" {}".format(MEDIA_PLAYER_PATH,MEDIA_PLAYER_BEFORE_ARGUMENTS,video_file_fullpath,MEDIA_PLAYER_AFTER_ARGUMENTS),shell=True)

        # Increment play_index and write play_index.txt in BASE_PATH.
        play_index = play_index + 1

    else:
        # Reset index at end of playlist.
        play_index = 0

    with open(os.path.join(BASE_PATH,"play_index.txt"),"w") as index_file:
        index_file.write(str(play_index))


#######################################################################
# Main loop.

if __name__ == "__main__":

    # If MEDIA_PLAYLIST is a file, open the file.
    if isinstance(MEDIA_PLAYLIST,str):
        with open(MEDIA_PLAYLIST,"r") as media_playlist_file:
            media_playlist = media_playlist_file.read().splitlines()

    elif isinstance(MEDIA_PLAYLIST,list):
        media_playlist = MEDIA_PLAYLIST

    else:
        raise Exception("MEDIA_PLAYLIST is not a file or Python list.")

    # Remove blank lines and comment entries in media_playlist.
    media_playlist = [i for i in media_playlist if i != ""
                      and not i.startswith(";")
                      and not i.startswith("#")
                      and not i.startswith("//")]

    while True:
        loop(media_playlist)
