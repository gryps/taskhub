def create_node_app():
    from taskhub_v2.node_agent.app import create_node_app as factory

    return factory()


__all__ = ["create_node_app"]
