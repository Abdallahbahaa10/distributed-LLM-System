"""
Hybrid RAG Retriever
- Tries ChromaDB first (for demo/report - shows sophistication)
- Falls back to simple retrieval if ChromaDB fails (for stability)
"""

# Simple fallback knowledge base
SIMPLE_KB = {
    "distributed": "Distributed computing uses multiple computers working together.",
    "load balancing": "Load balancing distributes requests across servers.",
    "fault tolerance": "Fault tolerance ensures systems continue despite failures.",
    "rag": "RAG enhances LLM responses with relevant context.",
    "gpu": "GPU clusters provide parallel processing for AI workloads.",
    "round robin": "Round robin distributes requests sequentially.",
    "master": "Master node coordinates worker nodes.",
    "vector": "Vector databases store embeddings for semantic search.",
    "grpc": "gRPC is a high-performance RPC framework.",
    "metrics": "Key metrics include latency, throughput, and error rate.",
}

def simple_retrieve(query: str) -> str:
    """Simple keyword-based retrieval (fallback)"""
    query_lower = query.lower()
    contexts = []
    
    for keyword, context in SIMPLE_KB.items():
        if keyword in query_lower:
            contexts.append(context)
    
    return " ".join(contexts) if contexts else "General distributed systems knowledge."

def retrieve_context(query: str) -> str:
    """
    Retrieve context using ChromaDB (preferred) or simple fallback
    """
    try:
        # Try ChromaDB first
        import chromadb
        from chromadb.utils import embedding_functions
        
        # Initialize ChromaDB client
        client = chromadb.Client()
        
        # Get or create collection
        try:
            collection = client.get_collection(name="knowledge_base")
        except:
            # Create and populate collection
            collection = client.create_collection(
                name="knowledge_base",
                embedding_function=embedding_functions.DefaultEmbeddingFunction()
            )
            
            # Add documents
            documents = [
                "Distributed computing involves multiple computers working together to solve complex problems.",
                "Load balancing distributes incoming requests across multiple servers to optimize resource utilization.",
                "Fault tolerance mechanisms ensure system availability even when components fail.",
                "RAG (Retrieval-Augmented Generation) enhances LLM responses by retrieving relevant context.",
                "GPU clusters provide massive parallel processing power for AI and machine learning workloads.",
                "Round Robin scheduling distributes tasks sequentially across available resources.",
                "Master node coordinates task distribution and monitors worker node health.",
                "Vector databases store embeddings for efficient semantic search and similarity matching.",
                "gRPC is a high-performance RPC framework using HTTP/2 and Protocol Buffers.",
                "Distributed system metrics include latency, throughput, availability, and error rates.",
            ]
            
            collection.add(
                documents=documents,
                ids=[f"doc_{i}" for i in range(len(documents))]
            )
        
        # Query the collection
        results = collection.query(
            query_texts=[query],
            n_results=2
        )
        
        if results and results['documents'] and results['documents'][0]:
            context = " ".join(results['documents'][0])
            print(f"[RAG] Using ChromaDB (vector search)")
            return context
        else:
            # No results from ChromaDB, use simple fallback
            print(f"[RAG] ChromaDB returned no results, using simple fallback")
            return simple_retrieve(query)
    
    except Exception as e:
        # ChromaDB failed, use simple fallback
        print(f"[RAG] ChromaDB error ({str(e)[:50]}), using simple fallback")
        return simple_retrieve(query)
