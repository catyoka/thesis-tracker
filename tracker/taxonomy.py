ANILIST_GENRES = tuple(
    """
Action
Adventure
Comedy
Drama
Ecchi
Fantasy
Horror
Mahou Shoujo
Mecha
Music
Mystery
Psychological
Romance
Sci-Fi
Slice of Life
Sports
Supernatural
Thriller
""".strip().splitlines()
)


ANILIST_TAGS = tuple(
    """
4-koma
Achromatic
Achronological Order
Acrobatics
Acting
Adoption
Advertisement
Afterlife
Age Gap
Age Regression
Agender
Agriculture
Airsoft
Alchemy
Aliens
Alternate Universe
American Football
Amnesia
Anachronism
Ancient China
Angels
Animals
Anthology
Anthropomorphism
Anti-Hero
Archery
Aromantic
Arranged Marriage
Artificial Intelligence
Asexual
Assassins
Astronomy
Athletics
Augmented Reality
Autobiographical
Aviation
Badminton
Ballet
Band
Bar
Baseball
Basketball
Battle Royale
Biographical
Bisexual
Blackmail
Board Game
Boarding School
Body Horror
Body Image
Body Swapping
Bowling
Boxing
Boys' Love
Brainwashing
Bullying
Butler
Calligraphy
Camping
Cannibalism
Card Battle
Cars
Centaur
CGI
Cheating
Cheerleading
Chibi
Chimera
Chuunibyou
Circus
Class Struggle
Classic Literature
Classical Music
Clone
Coastal
Cohabitation
College
Coming of Age
Conspiracy
Cosmic Horror
Cosplay
Cowboys
Creature Taming
Crime
Criminal Organization
Crossdressing
Crossover
Cult
Cultivation
Curses
Cute Boys Doing Cute Things
Cute Girls Doing Cute Things
Cyberpunk
Cyborg
Cycling
Dancing
Death Game
Delinquents
Demons
Denpa
Desert
Detective
Dinosaurs
Disability
Dissociative Identities
Dragons
Drawing
Drugs
Dullahan
Dungeon
Dystopian
E-Sports
Eco-Horror
Economics
Educational
Elderly Protagonist
Elf
Ensemble Cast
Environmental
Episodic
Ero Guro
Espionage
Estranged Family
Exiled
Exorcism
Fairy
Fairy Tale
Fake Relationship
Family Life
Fashion
Female Harem
Female Protagonist
Femboy
Fencing
Filmmaking
Firefighters
Fishing
Fitness
Flash
Food
Football
Foreign
Found Family
Fugitive
Full CGI
Full Color
Gambling
Gangs
Gekiga
Gender Bending
Ghost
Go
Goblin
Gods
Golf
Gore
Graduation Project
Guns
Gyaru
Handball
Henshin
Heterosexual
Hikikomori
Hip-hop Music
Historical
Homeless
Horticulture
Human Experimentation
Ice Sports
Idol
Incest
Indigenous Cultures
Inn
Inseki
Interspecies
Isekai
Iyashikei
Jazz Music
Josei
Judo
Kabuki
Kaiju
Karuta
Kemonomimi
Kids
Kingdom Management
Konbini
Kuudere
Lacrosse
Language Barrier
LGBTQ+ Themes
Long Strip
Lost Civilization
Love Triangle
Mafia
Magic
Mahjong
Maids
Makeup
Male Harem
Male Protagonist
Manzai
Marriage
Martial Arts
Matchmaking
Matriarchy
Medicine
Medieval
Memory Manipulation
Mermaid
Meta
Metal Music
Middle East
Military
Mixed Gender Harem
Mixed Media
Modeling
Monster Boy
Monster Girl
Mopeds
Motorcycles
Mountaineering
Musical Theater
Mythology
Natural Disaster
Necromancy
Nekomimi
Ninja
No Dialogue
Noir
Non-fiction
Nudity
Nun
Office
Office Lady
Oiran
Ojou-sama
Orphan
Otaku Culture
Outdoor Activities
Pandemic
Parenthood
Parkour
Parody
Philosophy
Photography
Pirates
Poker
Police
Politics
Polyamorous
Post-Apocalyptic
POV
Pregnancy
Primarily Adult Cast
Primarily Animal Cast
Primarily Child Cast
Primarily Female Cast
Primarily Male Cast
Primarily Teen Cast
Prison
Prophecy
Proxy Battle
Psychosexual
Puppetry
Rakugo
Real Robot
Rehabilitation
Reincarnation
Religion
Rescue
Restaurant
Revenge
Reverse Isekai
Robots
Rock Music
Rotoscoping
Royal Affairs
Rugby
Rural
Samurai
Satire
School
School Club
Scuba Diving
Seinen
Shapeshifting
Ships
Shogi
Short-Form Chapter
Shoujo
Shounen
Shrine Maiden
Skateboarding
Skeleton
Slapstick
Slavery
Snowscape
Software Development
Space
Space Opera
Spearplay
Steampunk
Stop Motion
Succubus
Suicide
Sumo
Super Power
Super Robot
Superhero
Surfing
Surreal Comedy
Survival
Swimming
Swordplay
Table Tennis
Tanks
Tanned Skin
Teacher
Teens' Love
Tennis
Terrorism
Time Loop
Time Manipulation
Time Skip
Tokusatsu
Tomboy
Torture
Tragedy
Trains
Transgender
Travel
Triads
Tsundere
Twins
Unrequited Love
Urban
Urban Fantasy
Vampire
Vertical Video
Veterinarian
Video Games
Vikings
Villainess
Virtual World
Vocal Synth
Volleyball
VTuber
War
Werewolf
Wilderness
Witch
Work
Wrestling
Writing
Wuxia
Yakuza
Yandere
Youkai
Yuri
Zombie
""".strip().splitlines()
)


def canonical_taxonomy_value(value: str, options: tuple[str, ...]) -> str:
    needle = str(value or "").strip().casefold()
    if not needle:
        return ""
    lookup = {option.casefold(): option for option in options}
    return lookup.get(needle, "")
