import subprocess
import atexit
import signal
import sys
from flask import Flask, render_template, request, jsonify
import requests
from urllib.parse import urlparse
from grab_favicon import favicon_url
from langchain_openai import ChatOpenAI
from langchain.agents import tool, AgentExecutor, OpenAIFunctionsAgent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.schema import SystemMessage, HumanMessage
from bs4 import BeautifulSoup
import re
from dotenv import load_dotenv  # For loading environment variables from a .env file
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

# Start the Docker container
docker_process = subprocess.Popen(
    ["docker", "run", "-p", "127.0.0.1:7000:7000", "-it", "cuoi/openserp", "serve", "-a", "0.0.0.0", "-p", "7000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

def cleanup_docker():
    print("Shutting down Docker container...")
    docker_process.terminate()
    docker_process.wait()
    print("Docker container shut down.")

# Register the cleanup function to be called on exit
atexit.register(cleanup_docker)

# Handle SIGINT (Ctrl+C) gracefully
def signal_handler(sig, frame):
    print("Ctrl+C detected. Cleaning up...")
    cleanup_docker()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

app = Flask(__name__)

# Initialize the language model
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

@tool
def search_google(query: str) -> list:
    """Search Google for the given query and return the results."""
    search_url = f"http://127.0.0.1:7000/google/search?text={query}&lang=EN&limit=7"
    response = requests.get(search_url)
    response.raise_for_status()
    results = response.json()
    
    # Add favicon URLs to each result
    favicon_cache = {}
    for result in results:
        domain = urlparse(result['url']).netloc
        if domain not in favicon_cache:
            favicon_cache[domain] = favicon_url(f"https://{domain}", use_default=True) # Using default favions. Change this to False if you want it to fetch the real favicons, at the cost of total speed.
        result['favicon'] = favicon_cache[domain]
    
    return results

@tool
def get_page_text(url: str) -> str:
    """Fetch the text content of a webpage."""
    try:
        response = requests.get(url, timeout=5)
    except requests.RequestException:
        return "Error fetching the page"
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Extract text
    text = soup.get_text()
    
    # Remove excessive newlines and whitespace
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    # Remove leading/trailing whitespace from each line
    text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())
    
    return text[:1000]  # Limit to first 1000 characters

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/favicon', methods=['GET'])
def get_favicon():
    domain = request.args.get('domain', '')
    favicon_url_result = favicon_url(domain)
    return jsonify({"favicon_url": favicon_url_result})

def today():
    # Get the current date
    current_date = datetime.now()
    
    # Format the day of the week
    day_of_week = current_date.strftime("%A")
    
    # Format the month
    month = current_date.strftime("%B")
    
    # Get the day of the month
    day = current_date.day
    
    # Add the appropriate suffix to the day
    if 4 <= day <= 20 or 24 <= day <= 30:
        suffix = "th"
    else:
        suffix = ["st", "nd", "rd"][day % 10 - 1]
    
    # Format the year
    year = current_date.strftime("%Y")
    
    # Combine all parts into the final string
    return f"{day_of_week}, {month} {day}{suffix}, {year}"

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '')
    print("query: ", query)
    
    try:
        # Construct a full question for the agent
        full_query = f"Please answer the following question: {query}"
        print("Full query:", full_query)
        
        # Define the agent
        tools = [search_google, get_page_text]

        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=f"You are an intelligent search assistant. Today is {today()}. Use the provided tools to search for information and answer user queries. If you can't find a direct answer from the search results, use the get_page_text tool to fetch more information from the most relevant URL. When you provide your answer, you must provide a source at the end, as hyperlink in markdown format, where the given url will lead the user directly to the information you are citing. You might need to use the google search tool more than once if the query is complex."),
            HumanMessage(content=full_query),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent = OpenAIFunctionsAgent(llm=llm, tools=tools, prompt=prompt)
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
        
        # Use the agent to perform the search and get the answer
        result = agent_executor.invoke({"input": full_query})
        answer = result["output"]
        
        # Perform a regular search to get the search results for display
        search_results = search_google(query)
        
        return jsonify({
            "answer": answer,
            "results": search_results
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5001, debug=True)
