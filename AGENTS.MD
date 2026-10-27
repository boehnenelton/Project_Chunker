###
complete latest: tasks and do some extensive testing and verify

Create A CLI folder, Move the chunker in there. its going to be a modular system utolize  separation of concerns 

Create lib_bejson_projects family, Any potentially reusable logic belongs in the new projects family. 

Build a menu wrapper tool, This tool will also export a configuration file story script locations and then relative path to each other so the menu tool can wrap the CLI tool for now it'll only have the chunking and commands


-----

update 
-------
i copied the libraries integrate them validate everything

original prompt
-------
# TASK 1

ATTTACHED IS TWO SEPERATE CHUNKER
/storage/emulated/0/Admin/tools/Cli_Chunker-Latest

/storage/emulated/0/Admin/repos/new/MFDB_Chunker-V633-PKG1

~/Env_Manager/Installation

 SYSTEMS AND MY ENVIRONMENT SYSTEM. STRICTLY FOR CONTEXT. DO NOT DO ANYTHING WITH THEM BUT STUDY THEM.

STUDY THE CHUNKER SYSTRMS AND THE TRACKER SYSTEM AND SCHEMA SPECIFICALLY. 

SUMMARIZE YOUR FINDINGS OF EACH OF THE 3 SYSTEMS THEN STAND BY AND WAIT FOR YOUR NEXT INSTRUCTIONS. 

---

# TASK 2

We are going to be designing a new hybridization of a Chunker / Project Management and Tracking system. It will be a dual MFDB system with a CLI tool at its base. We design the CLI tool first step by step.

THEORY: The system will be backed by the following three MFDB structurally pillats...


## Tracker Detection and Capture 
--chunk --track

---
(Chunker Root)/
/storage/trackers/data/(project_name).bejson

- If you use the --track flag, when you chunk a directory the first thing it will attempt to do is identify if there is a tracker in the directory. If there is not Then it will simply take the directory name, replace spaces with _ and assign a new tracker with the directory name as the project_name, assigning it version 100. If it detect V#-PKG# In the name it will use that version instead.  If there is a tracker it will use my name in version.

it's then captures just the tracker file, creates a record in the new or previously created project specific entity, stores the tracker and newly created UUID and a record of the project entity in the tracker database.

---  
(Chunker Root)//storage/master/data/registry.bejson

After storing the tracker and UUID In the project entity of the Tracker database it takes that UUID, and registers it keying it too the the tracker file by UUID


---
(Chunker Root)/storage/project_snapshots/(project_name)/data/(project_version).bejson
this will create a new mfdb for each project by its project name and it's on Project name folder and then each entity will contain the snapshot chunk of the project key back to the registry and its tracker




---- 


build this in ~/project_chunker