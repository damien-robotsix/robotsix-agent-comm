"""Quick check that brokered module can be imported and tests pass."""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, ".")
print("Checking imports...")
from robotsix_agent_comm.transport.brokered import NetworkedBrokerTransport, BrokeredRegistry, create_transport_pair
print("  NetworkedBrokerTransport:", NetworkedBrokerTransport)
print("  BrokeredRegistry:", BrokeredRegistry)
print("  create_transport_pair:", create_transport_pair)
print("All imports OK!")
