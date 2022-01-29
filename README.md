# tablemap
Tablemap is a Python data wrangling tool for those who feels 'pandas' is too complex to learn. For example, you have the quarterly accounting data set for listed companies in the U.S. For each quarter, you want to compute the firm-level seasonal average sales growth rate for the past 6 years. Then you will merge this data set with the market information, however it is too large in size to be loaded on your laptop memory at the same time. 

Of course pandas can do this. The problem, at least for me, is that for every little detail in the task, you may feel you are beginning from the zero base all over, i.e., google it, copy and paste, no idea how it works exactly, this seemingly infinite loop. Your skills don't seem to be snowballing. You barely learned Python. Even that wasn't easy at all. Why can't we just do this mundane job with only lists and dictionaries? You want to grab hold of the whole workflow with those things you are already familiar with. 

One more thing. You have written a script of about 1k lines of code. Next day, you realize that you missed one variable to add in the middle of the process. You don't want to rerun the whole script. It took about 5 hours. It would be great if we can execute only the part which is affected by this modification in the script. 

<!-- Tablemap accomplishes three purposes. 

- Data handling can be done with only Python lists and dictionaries.
- No worries about the memory
- Only the necessary part of the script should be run

Instead of loading the data on memory as in pandas, tablemap makes use of database files(Sqlite3). It means tablemap may not be so performant in terms of process time. In order to make up for this loss, it is almost trivial to take advantages of multicore processes in tablemap. 
 -->


# Install
pip install tablemap

# 
[Documentation]
(https://tablemap.readthedocs.io/en/latest/index.html)