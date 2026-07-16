#asynchrounous programming
#program can handle multiple tasks at the same time
#Should use when there are tasks that wait a lot
#can handle many tasks concurrently without using CPU power - makes more efficient and responsive
#threads are used for tasks that are I/O bound and require waiting and data nee to be shared between tasks
#Process - CPU heavy tasks
#each process runs independanly in parallel across multuple cores

#Comparison
#Use Async for I/O bound tasks that involve waiting
#using Processing for maximizing performance on CPU intensive tasks
#Use threads for tasks that share data with minimal CPU use


#Event loop - core that manages and didstributes tasks, central hub with tasks circling around waiting to be 
#executed
#keeps program running efficiently - 

import asyncio
from typing import assert_never

#coroutine function
#main()->Coroutine object

# async def main():
#     print('Start of main coroutine')

#Run the main coroutine using the event loop
# asyncio.run(main())
    
#if you call a function with the async keyword, it returns a coroutine object, not the result of the function
#the coroutine object needs to be awaited in order to actually execute
#The asyncio.run syntax to handle the awaiting the coroutine and allow us to write more asynchrounous code

# asyncio.run(main())

#I/O operatioins - waiting on something from the network like API calls or reading a file 

# async def fetch_data(delay):
#     print('Fetching data...')
#     await asyncio.sleep(delay) #simulating I/O operation with a sleep 
#     print('Data fetched')
#     return {'data': 'some data'}

# async def main():
#     print('start of main coroutine')
#     task = fetch_data(2) #this task the coroutine object that is created but not yet executed 
#     result = await task #task will be awaited before rest of the code starts executing 
#     print(f"recieved result: {result}")
#     print('End of main coroutine')

# asyncio.run(main())

# #variation 
# async def fetch_data(delay):
#     print('Fetching data...')
#     await asyncio.sleep(delay) #simulating I/O operation with a sleep 
#     print('Data fetched')
#     return {'data': 'some data'}

# async def main():
#     print('start of main coroutine')
#     task = fetch_data(2) 
#     print('End of main coroutine')
#     result = await task #here we waited for the execution to finish before moving onto the next line 
#     #end of main coroutine was executed before the fetch_data function finished
#     print(f"recieved result: {result}")
#     #coroutine is not executed until it is awaited or if it is wrapped in a task


# async def fetch_data(delay, id):
#     print('Fetching data.. id', id)
#     await asyncio.sleep(delay)
#     print('data fetched, id:', id)
#     return {'data': 'some data', 'id': id}

# async def main():
#     task1 = fetch_data(2, 1)
#     task2 = fetch_data(2, 2)

#     result1 = await task1
#     print(f"recieved result: {result1}")

#     result2 = await task2
#     print(f"recieved result: {result2}")

# asyncio.run(main())

#output:
# fetching data.. id 1
# data fetched, id: 1
# recieved result: {'data': 'some data', 'id': 1}
# Fetching data.. id 2
# data fetched, id: 2
# recieved result: {'data': 'some data', 'id': 2}

#task allows us to schedule a coroutine and run multiple coroutines at the same time
#oreviously needed to wait until one coroutine to finish until the other one finished
#will not be executing tasks the same time, but if one task is taking a long time such as waiting for data to 
#be sent, it will switch over to another task immediatley 
#always attempting to do something

#Now running tasks concurrently

# async def fetch_data(id, sleep_time):
#     print(f"Coroutine {id} starting to fetch data.")
#     await asyncio.sleep(sleep_time)
#     return {'id': id, 'data': f"Sample data from coroutine {id}"}

# async def main():
#     task1 = asyncio.create_task(fetch_data(1,2))
#     task2 = asyncio.create_task(fetch_data(2,3))
#     task3 = asyncio.create_task(fetch_data(3,1))

#     result1 = await task1
#     result2 = await task2
#     result3 = await task3
# #allowing multiple coroutines to run at the same time
# #when one coroutine is waiting on something, the other one will run 
#     print(result1, result2, result3)

# asyncio.run(main())

#gather function 

# async def fetch_data(id, sleep_time):
#     print(f"Coroutine {id} starting to fetch data")
#     await asyncio.sleep(sleep_time)
#     return {"id": id, "data": f"Sample data from corouting {id}"}

# async def main():
#     results = await asyncio.gather(fetch_data(1,2), fetch_data(2,1), fetch_data(3,3))
#     #will automatically schedule to run concurrenlty and gather order in a list - will await for all of them
#     #gather is not good at error handling and it won't cancel other coroutines if one of them fails - can get weird
#     #state if exceptions or errors occurs

#     for result in results:
#         print (f"recieved results: {result}")

#Using TaskGroup because it is better for error handling 
# async def fetch_data(id, sleep_time):
#     print(f"Coroutine {id} starting to fetch data")
#     await asyncio.sleep(sleep_time)
#     return {"id": id, "data": f"Sample data from corouting {id}"}

# async def main():
#     tasks = []
#     async with asyncio.TaskGroup() as tg: #async context manager - gives access to tg variable
#         #create task in task group and automatically execute all the tasks in the task group and retrieve
#         #all of the results in the list
#         #run tasks when want to execute code concurrently and want things to happen at the same time
#         for i, sleep_time in enumerate([2,1,3], start = 1):
#             task = tg.create_task(fetch_data(i, sleep_time))
#             tasks.append(task)
#     results = [task.result() for task in tasks]

#     for result in results:
#         print(f"recieived result: {result}")

#Shared resource
#Want to make sure that no two coroutines are working on the same resource at the same time 
#might get mutated state because different operations are happening at different times, 
#want one operation to finish before 
shared_resource = 0

lock = asyncio.Lock()

async def modify_shared_resource():
    global shared_resource
    async with lock: #check if any other coroutine is using the lock - it will wait until that coroutine is finished
        #anything in the context manager needs to finish before the lock is released
        #synchronousing coroutines so that they cannot be using this block of code while another coroutine is 
        #executing it 
        #critical section starts
        print(f"Resource before modification {shared_resource}")
        shared_resource += 1
        await asyncio.sleep(1)
        print(f"resource after modification: {shared_resource}")
        #crititcal section ends
async def main():
    await asyncio.gather(*(modify_shared_resource() for _ in range(5)))

asyncio.run(main())