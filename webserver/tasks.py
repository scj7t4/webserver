# Some Fancy Tasks and Things For Hermes!

import celery
import slumber
import json
from datetime import datetime,timedelta
from competition.models.game_model import Game, GameScore
from competition.models.team_model import Team
from django.db.models import Q

ARENA_API_URL = "http://r13tauritzd.managed.mst.edu/thunderdome/api/v1/"
SCHEDULED_STRING = "Scheduled"
COMPLETED_STRING = "Complete"
RUNNING_STRING = "Running"
FAILED_STRING = "Failed"
NEW_STRING = "New"
HAS_STARTED = [COMPLETED_STRING,RUNNING_STRING,FAILED_STRING]
HAS_FINISHED = [COMPLETED_STRING,FAILED_STRING]
HAS_NOT_FINISHED = [NEW_STRING,SCHEDULED_STRING,RUNNING_STRING]

def populate_score(slumber, game=None):
    # Fetch the team object based on the slug of the team name
    # XXX: I could have sworn this was slug, but now it seems to be the actual name
    team = Team.objects.get(name=slumber["name"]["name"])
    
    # There is no game object provided, create a new gamescore
    if game == None:
        score = GameScore()
        score.team = team
    # Or look up an old one
    else:
        # Apparently we can catch a game where there is only one team, when
        # in reality, there are two.
        (score,c) = GameScore.objects.get_or_create(game=game,team=team)
    
    # If the team won the game give them a point
    if slumber["won"] == True:
        score.score = 1
    #: Not set to false if a team loses, as it turns out.
    elif slumber["output_url"]:
        score.score = 0
    else:
        score.score = None
    
    # Set extra data for the glog.
    extra = score.data
    up = {'output_url': slumber['output_url'],
                  'version': slumber['version']}
    try:
        extra.update(up)
    except AttributeError:
        extra = up

    score.extra_data = json.dumps(extra)
    return score

#@celery.task
def fetch_games(competition,offset=0,at_a_time=60,max_to_fetch=100000,max_time=None):
    # Fetch a bunch of games
    start_time = datetime.today()
    found_one_new = True
    for load in xrange(offset,max_to_fetch,at_a_time):
        #If we go through a whole page without a new game being found, quit.
        if found_one_new == False:
            break
        found_one_new = False
        if max_time != None and datetime.today()-start_time > timedelta(seconds=max_time):
            break
        # Query the arena api
        api = slumber.API(ARENA_API_URL)
        obj = api.game.get(offset=load,limit=at_a_time)
        if len(obj["objects"]) == 0:
            break
        print "Loading offset {}".format(load)
        for game in obj["objects"]:
            scores = []
            # Populate a score object for this match
            try:
                for team in game["game_data"]:
                    score = populate_score(team,None)
                    scores.append(score)
            # Handle if the team cannot be found.
            except Team.DoesNotExist:
                print "Could not find team: {}".format(team["name"]["name"])
                continue
            
            # Try to load or create a game object using get_or_create
            status = game['status']
            extra = {'gamelog_url': game["gamelog_url"]}
            (x,c) = Game.objects.get_or_create(game_id=game["id"],competition=competition,
                    defaults = {
                        'start_time' : datetime.today() if status in HAS_STARTED else None,
                        'end_time'   : datetime.today() if status in HAS_FINISHED else None,
                        'status'     : status,
                        'extra_data' : json.dumps(extra),
                        })
            found_one_new = found_one_new or c
            # If the object is created, then save it and the related score objecs
            if c:
                x.save()
                for score in scores:
                    score.game = x
                    score.save()
                

def update_games(competition, offset=0, at_a_time=20, max_updates=10000, max_time=None):
    # Start an empty object
    q_filter = None
    # For every status type that indicates the game ended
    for status in HAS_NOT_FINISHED:
        # Or the two status types together
        t = Q(status=status)
        if q_filter != None:
            q_filter = q_filter | t 
        else:
            q_filter = t
        
    start_time = datetime.today()
    for load in xrange(offset,max_updates,at_a_time):
        if max_time != None and datetime.today()-start_time > timedelta(seconds=max_time):
            break
        # Load all the games that haven't marked as finished (using the filter)
        unfinished_games = Game.objects.filter(q_filter).filter(competition=competition).order_by('-pk')[load:load+at_a_time]
        api = slumber.API(ARENA_API_URL)
        
        for game in unfinished_games:
            # Query the api
            obj = api.game(game.game_id).get()
            scores = []
            status = obj["status"]
            # See if the game status is changed
            if status != game.status:
                # Populate new score objects from the server
                for team in obj["game_data"]:
                    score = populate_score(team,game)
                    scores.append(score)
                # If we haven't recorded a start time, see if we should.
                if game.start_time == None:
                    game.start_time = datetime.today() if status in HAS_STARTED+HAS_FINISHED else None
                # Same for the end time.
                if game.end_time == None:
                    game.end_time = datetime.today() if status in HAS_FINISHED else None
                if status in HAS_FINISHED:
                    data = game.data
                    data.update({'gamelog_url': obj["gamelog_url"]})
                    game.extra_data = json.dumps(data)
                game.status = status
                game.save()
                # Update the related score objects
                for score in scores:
                    score.save()