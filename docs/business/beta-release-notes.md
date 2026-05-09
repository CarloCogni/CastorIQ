We just had a call we our mentor today who approved the plan to actually go live in a sort of beta testing invite-only 
environment during zigurat's students week in barcelona mid june. 
We already had a pre-plan in docs/business/public-beta-barcelona-2026.md, i would like you to review it based on the 
following considerations. Also in parallel i would like to update the document docs/business/live-roadmap.md with all the 
milestones necessary to actually deliver the project live.
I just bought a domain name in namescheap. Now we have to discuss where to rent the VPS server, configurations, etc...etc...


Here is the notes:

the idea is to have a landing page where the user can join the waitlist/ ask to join the beta testing.
The point here is that i don't want to have users registering automatically, neither just having a waitlist with nothing.
I would like to have control to a certain extent to who is joining in. Potentially to everyone interested, i would like }
to do a personal video call, 10 min just to get to know the person, see what they are looking for, and pontentially guide them
in finding what they need. Once the call is made, they are manually registered in the admin and they can login
SO they idea is you leave email and other informations (maybe like name, job, description, etc.....) on the main landing page,
i review the candidates and either i make the new user straiht away or i schedule for a call. 

In the landing page there should also be the a link to the video. it should point to youtube/canavs or whatever to show
the presentation video, this way even for high traffic it does not impact our VSP.

the deadline to have castor live and running is the end of may.

I would still connect claude ( for high quality reasonining in _ask.html ) and groq fro afster responses and for the 
_modify pipeline ( llama70b it's a much more powerfull model compared to what we are testing on). very important to keep 
token consumption under control, and let the user understand this by giving hard constraints for testing purposes.
Perhaps we coud alreayd have the user give it's personal API keys if he wants to use it more in depth, and without normal 
restrictions.
Important to manage is token awareness, for the user so it does no burn too much of my credit, but also to keep _ask.html 
dinamic and usefull, since it could potentially pull from ifc models and documents hundred of thousands of tokens. 
We already had something in place, we have to be carefull!

Definetely worth adding a sample project for every user with the same ifc file for testing immediatly without any friction

I'm worried about embedding done in the CPU in VPS, that could crash?? it could slow down?
perhaps it's better to outsorce this as well?

REMEBER the idea is to be able to deliver as quick as possible. Idieally i would like to have castor live tomorrow.
So let's focus on the things that actually are important first to accomplish this. The final result will never be
perfect, but that's not what this is about. Keep in mind the concepts of the lean startup by Eric Ries

the point of the live roadmap is to have the milestones to work on one by one and then to check and move forward. 

Perhaps there are other documents worth creating? anything else? be creative


#################################################################################

next: dry-run-check list to double check before running. adding api keys from claude and groq.
Double checking how quickly code updates can be pushed to production.
push to production

to check in production: backups, how to connect to external data saving source to expand like server + NAS

power BI

connect external providers for monitoring
connect email
 keep on going!!!!

settings.html -> needs to be adjusted to be adjusted
-----

final vps bring up: 
mail GUN 
Sentry

settings.html - adjust
implement power BI
test groq and Claude connections

bait and switch landing page

test restore backup after night cronjob


websocket errors logger
400, 404, 500 html pages!

other user experience requirements? 

HETZNER - investiage external volumes for back and media storage like a NAS