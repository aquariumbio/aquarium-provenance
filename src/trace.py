import abc
import argparse
import json
from pydent import AqSession
from provenance.aquarium import TraceFactory
from resources import resources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--plan_id",
                        help="the ID of the plan",
                        required=True)
    parser.add_argument("-o", "--output",
                        help="the output file",
                        required=True)
    args = parser.parse_args()

    session = AqSession(
        resources['login'], resources['password'], resources['aquarium_url'])

    plan = session.Plan.find(args.plan_id)

    trace = TraceFactory.create_from(session=session, plan=plan)

    with open(args.output, 'w') as file:
        file.write(json.dumps(trace.as_dict(), indent=2))


if __name__ == "__main__":
    main()
